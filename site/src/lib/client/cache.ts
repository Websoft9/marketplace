// Browser-side data loader with a memory cache, a persistent localStorage
// cache, browser HTTP cache revalidation (ETag / Last-Modified) and a
// stale-while-revalidate refresh strategy.
//
// Caching policy per source is supplied by the caller:
//   * fresh entry       -> returned immediately, no network request
//   * stale entry       -> returned immediately, refreshed in the background
//                          through the browser HTTP cache (ETag revalidation)
//   * missing entry     -> fetched before the page renders
//   * failed refresh    -> the cached entry is kept when it is still usable

const STORAGE_PREFIX = "w9mp:data:v1:";
const STORAGE_FAMILY_PREFIX = "w9mp:";
const REQUEST_TIMEOUT_MS = 12_000;
const RETRY_ATTEMPTS = 2;

export interface ResourceMeta {
  /** Epoch milliseconds of the response (or conditional revalidation). */
  fetchedAt: number;
  /** True when cached data was returned while a background refresh runs. */
  stale: boolean;
}

export interface ResourceResult<T> {
  data: T;
  meta: ResourceMeta;
}

export interface ResourceSpec<T> {
  /** Stable cache key; also used as the storage suffix. */
  key: string;
  url: string;
  /** How long a stored response is considered fresh. */
  ttlMs: number;
  /** Structural validation and normalization of the JSON payload. */
  parse: (value: unknown) => T;
  /** Called when a background refresh produced different data. */
  onUpdate?: (data: T, meta: ResourceMeta) => void;
  /** Called after a successful background revalidation, even if unchanged. */
  onRefresh?: (meta: ResourceMeta) => void;
}

interface CacheRecord {
  url: string;
  storedAt: number;
  etag?: string;
  data: unknown;
}

interface FetchedRecord<T> {
  record: CacheRecord;
  data: T;
  changed: boolean;
}

class HttpError extends Error {
  constructor(readonly status: number) {
    super(`HTTP ${status}`);
    this.name = "HttpError";
  }

  get retryable(): boolean {
    return this.status >= 500 || this.status === 408 || this.status === 429;
  }
}

const memoryCache = new Map<string, CacheRecord>();
const inFlight = new Map<string, Promise<FetchedRecord<unknown>>>();

function openStorage(): Storage | undefined {
  try {
    if (typeof localStorage === "undefined") return undefined;
    return localStorage;
  } catch {
    return undefined;
  }
}

function readCached(key: string): CacheRecord | undefined {
  const inMemory = memoryCache.get(key);
  if (inMemory) return inMemory;

  const storage = openStorage();
  if (!storage) return undefined;

  try {
    const raw = storage.getItem(STORAGE_PREFIX + key);
    if (!raw) return undefined;
    const record = JSON.parse(raw) as CacheRecord;
    if (
      !record ||
      typeof record.url !== "string" ||
      typeof record.storedAt !== "number" ||
      !("data" in record)
    ) {
      storage.removeItem(STORAGE_PREFIX + key);
      return undefined;
    }
    memoryCache.set(key, record);
    return record;
  } catch {
    storage.removeItem(STORAGE_PREFIX + key);
    return undefined;
  }
}

function writeCached(key: string, record: CacheRecord): void {
  memoryCache.set(key, record);

  const storage = openStorage();
  if (!storage) return;

  const storageKey = STORAGE_PREFIX + key;
  try {
    storage.setItem(storageKey, JSON.stringify(record));
    return;
  } catch {
    // Most likely the 5 MB quota was exceeded: drop the oldest entries once
    // and retry. Persistent caching is an optimization, never a requirement.
    pruneStorage(storage);
    try {
      storage.setItem(storageKey, JSON.stringify(record));
    } catch {
      storage.removeItem(storageKey);
    }
  }
}

function pruneStorage(storage: Storage): void {
  const entries: Array<{ key: string; storedAt: number }> = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (!key || !key.startsWith(STORAGE_FAMILY_PREFIX)) continue;
    let storedAt = 0;
    try {
      storedAt = Number(JSON.parse(storage.getItem(key) ?? "{}").storedAt) || 0;
    } catch {
      storedAt = 0;
    }
    entries.push({ key, storedAt });
  }

  entries.sort((a, b) => a.storedAt - b.storedAt);
  const removeCount = Math.max(1, Math.ceil(entries.length / 2));
  for (const entry of entries.slice(0, removeCount)) {
    storage.removeItem(entry.key);
  }
}

export function clearCache(): void {
  memoryCache.clear();
  const storage = openStorage();
  if (!storage) return;

  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key && key.startsWith(STORAGE_FAMILY_PREFIX)) keys.push(key);
  }
  for (const key of keys) storage.removeItem(key);
}

function tryParse<T>(spec: ResourceSpec<T>, record: CacheRecord): T | undefined {
  try {
    return spec.parse(record.data);
  } catch {
    // A cached payload that no longer validates (schema change, partial write)
    // must not block a fresh request.
    return undefined;
  }
}

function timeoutSignal(): AbortSignal | undefined {
  if (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal) {
    return AbortSignal.timeout(REQUEST_TIMEOUT_MS);
  }
  if (typeof AbortController === "undefined") return undefined;
  const controller = new AbortController();
  setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  return controller.signal;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function request(url: string): Promise<Response> {
  let lastError: unknown;

  for (let attempt = 1; attempt <= RETRY_ATTEMPTS; attempt += 1) {
    try {
      // Conditional request headers (If-None-Match / If-Modified-Since) are not
      // CORS-safelisted and would trigger a preflight request that public
      // artifact hosts may reject. `no-cache` instead lets the browser
      // revalidate its HTTP cache entry with the validators it stored.
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        cache: "no-cache",
        credentials: "omit",
        signal: timeoutSignal(),
      });

      if (response.ok || response.status === 304) return response;
      throw new HttpError(response.status);
    } catch (error) {
      lastError = error;
      if (error instanceof HttpError && !error.retryable) throw error;
      if (attempt < RETRY_ATTEMPTS) {
        await delay(attempt * 500);
      }
    }
  }

  throw lastError instanceof Error
    ? lastError
    : new Error(`无法加载 ${url}`);
}

async function fetchRecord<T>(
  spec: ResourceSpec<T>,
  cached: CacheRecord | undefined,
): Promise<FetchedRecord<T>> {
  const response = await request(spec.url);

  // Browsers normally resolve a 304 into the stored 200 response, but keep a
  // defensive path in case a 304 ever surfaces to the page.
  if (response.status === 304 && cached) {
    const record: CacheRecord = { ...cached, storedAt: Date.now() };
    writeCached(spec.key, record);
    return {
      record,
      data: spec.parse(cached.data),
      changed: false,
    };
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`${spec.url} 返回的内容不是有效的 JSON。`);
  }

  const data = spec.parse(payload);
  const record: CacheRecord = {
    url: spec.url,
    storedAt: Date.now(),
    etag: response.headers.get("etag") ?? undefined,
    data,
  };
  writeCached(spec.key, record);

  const changed = !cached
    ? true
    : record.etag && cached.etag
      ? record.etag !== cached.etag
      : JSON.stringify(cached.data) !== JSON.stringify(data);

  return { record, data, changed };
}

function revalidate<T>(spec: ResourceSpec<T>, cached?: CacheRecord): Promise<FetchedRecord<T>> {
  const pending = inFlight.get(spec.key) as Promise<FetchedRecord<T>> | undefined;
  if (pending) return pending;

  const promise = fetchRecord(spec, cached).finally(() => {
    inFlight.delete(spec.key);
  });
  inFlight.set(spec.key, promise as Promise<FetchedRecord<unknown>>);
  return promise;
}

function refreshInBackground<T>(spec: ResourceSpec<T>, cached: CacheRecord): void {
  void revalidate(spec, cached)
    .then(({ record, data, changed }) => {
      const meta: ResourceMeta = { fetchedAt: record.storedAt, stale: false };
      spec.onRefresh?.(meta);
      if (changed) {
        spec.onUpdate?.(data, meta);
      }
    })
    .catch((error: unknown) => {
      console.warn(
        `[marketplace] 后台更新 ${spec.key} 失败，继续使用缓存。`,
        error,
      );
    });
}

export async function loadResource<T>(
  spec: ResourceSpec<T>,
): Promise<ResourceResult<T>> {
  const cached = readCached(spec.key);

  if (cached && cached.url === spec.url) {
    const data = tryParse(spec, cached);
    if (data !== undefined) {
      const age = Date.now() - cached.storedAt;
      if (age < spec.ttlMs) {
        return { data, meta: { fetchedAt: cached.storedAt, stale: false } };
      }

      refreshInBackground(spec, cached);
      return { data, meta: { fetchedAt: cached.storedAt, stale: true } };
    }
  }

  const { record, data } = await revalidate(spec);
  return { data, meta: { fetchedAt: record.storedAt, stale: false } };
}
