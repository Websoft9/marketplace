import {
  isProductPlatform,
  type PlatformConfig,
  type PlatformKey,
  type ProductPlatformConfig,
  type StoreConfig,
} from "./config";
import type { CatalogProduct, MarketplaceData } from "./marketplace";
import { asString, asUrl, asWebsiteUrl, isRecord } from "./value";

export type ProductMatchStatus = "matched" | "mapped" | "unmatched";
export type ProductSource = "artifact" | "matrix" | "unavailable";
export type ArtifactStatus = "complete" | "partial" | "error" | "missing";

export interface PlatformProductRow {
  id: string;
  platform: PlatformKey;
  title: string;
  url?: string;
  code: string;
  entityId?: string;
  appId?: string;
  appName?: string;
  appWebsiteUrl?: string;
  appInCatalog: boolean;
  matchStatus: ProductMatchStatus;
  storeId?: string;
  provider?: string;
  visibility?: string;
  shortDescription?: string;
  source: ProductSource;
}

export interface PlatformProductStats {
  total: number;
  matched: number;
  mapped: number;
  unmatched: number;
  withApp: number;
  apps: number;
}

export interface PlatformProductArtifact {
  url: string;
  status: ArtifactStatus;
  queriedAt?: string;
  storeProvider?: string;
  visibility?: string;
  warnings: string[];
  message?: string;
}

export interface PlatformProductData {
  platform: string;
  label: string;
  description: string;
  stores: StoreConfig[];
  searchUrlPrefix?: string;
  rows: PlatformProductRow[];
  stats: PlatformProductStats;
  source: ProductSource;
  artifact?: PlatformProductArtifact;
}

export interface PlatformProductsTotals {
  platforms: number;
  products: number;
  matched: number;
  mapped: number;
  unmatched: number;
  apps: number;
}

interface ArtifactProduct {
  code?: unknown;
  title?: unknown;
  url?: unknown;
  entityId?: unknown;
  storeId?: unknown;
  provider?: unknown;
  shortDescription?: unknown;
  visibility?: unknown;
  appMatch?: unknown;
}

export interface GenerationArtifact {
  platform?: unknown;
  products?: unknown;
  inventory?: unknown;
  matching?: unknown;
  visibility?: unknown;
}

export interface MatrixEntry {
  appId: string;
  appName: string;
  appInCatalog: boolean;
  websiteUrl?: string;
  code: string;
  url?: string;
}

export interface ArtifactLoad {
  platform: PlatformConfig;
  artifact?: GenerationArtifact;
  error?: string;
}

interface ParsedArtifact {
  rows: PlatformProductRow[];
  complete: boolean;
  queriedAt?: string;
  storeProvider?: string;
  visibility?: string;
  warnings: string[];
}

/** Validates the top-level shape of a generation report before parsing it. */
export function parseArtifactPayload(value: unknown): GenerationArtifact {
  if (!isRecord(value)) {
    throw new Error("商品制品必须是 JSON 对象。");
  }
  return value as GenerationArtifact;
}

function normalizeCode(code: string): string {
  return code.trim().toLowerCase();
}

function compareRows(a: PlatformProductRow, b: PlatformProductRow): number {
  return (
    a.title.localeCompare(b.title, "zh-CN", {
      numeric: true,
      sensitivity: "base",
    }) || a.code.localeCompare(b.code)
  );
}

function calculateStats(rows: PlatformProductRow[]): PlatformProductStats {
  const matched = rows.filter((row) => row.matchStatus === "matched").length;
  const mapped = rows.filter((row) => row.matchStatus === "mapped").length;
  const unmatched = rows.filter((row) => row.matchStatus === "unmatched").length;
  const apps = new Set(
    rows.flatMap((row) => (row.appId ? [row.appId] : [])),
  ).size;

  return {
    total: rows.length,
    matched,
    mapped,
    unmatched,
    withApp: matched + mapped,
    apps,
  };
}

function catalogName(
  catalogByKey: Map<string, CatalogProduct>,
  appId: string | undefined,
): string | undefined {
  if (!appId) return undefined;
  return catalogByKey.get(appId)?.trademark || undefined;
}

function parseArtifact(
  platform: ProductPlatformConfig,
  artifact: GenerationArtifact,
  matrixByCode: Map<string, MatrixEntry>,
  catalogByKey: Map<string, CatalogProduct>,
): ParsedArtifact | undefined {
  const artifactPlatform = asString(artifact.platform);
  if (artifactPlatform && artifactPlatform !== platform.key) return undefined;
  if (!Array.isArray(artifact.products)) return undefined;

  const inventory = isRecord(artifact.inventory) ? artifact.inventory : undefined;
  const inventoryStore =
    inventory && isRecord(inventory.store) ? inventory.store : undefined;
  const inventoryScope =
    inventory && isRecord(inventory.scope) ? inventory.scope : undefined;
  const matching = isRecord(artifact.matching) ? artifact.matching : undefined;
  const complete = inventory?.complete === true;
  const expectedCount =
    typeof inventory?.count === "number" ? inventory.count : undefined;
  const inventoryErrors = Array.isArray(inventory?.errors)
    ? inventory.errors.length
    : 0;
  const seen = new Set<string>();
  const rows: PlatformProductRow[] = [];
  const warnings: string[] = [];
  let skipped = 0;

  for (const raw of artifact.products) {
    if (!isRecord(raw)) {
      skipped += 1;
      continue;
    }

    const product = raw as ArtifactProduct;
    const code = asString(product.code);
    if (!code) {
      skipped += 1;
      continue;
    }

    const codeKey = normalizeCode(code);
    if (seen.has(codeKey)) {
      skipped += 1;
      continue;
    }
    seen.add(codeKey);

    const appMatch = isRecord(product.appMatch) ? product.appMatch : undefined;
    const matchStatus = asString(appMatch?.status);
    const artifactAppId =
      matchStatus === "matched" ? asString(appMatch?.appId) : undefined;
    const matrix = matrixByCode.get(codeKey);
    const appId = artifactAppId ?? matrix?.appId;
    const rowStatus: ProductMatchStatus = artifactAppId
      ? "matched"
      : matrix
        ? "mapped"
        : "unmatched";
    const catalogProduct = appId ? catalogByKey.get(appId) : undefined;
    const appName =
      catalogProduct?.trademark || matrix?.appName || appId;
    const title = asString(product.title) ?? appName ?? code;
    const url = asUrl(product.url) ?? matrix?.url;

    rows.push({
      id: `${platform.key}:${codeKey}`,
      platform: platform.key,
      title,
      url,
      code,
      entityId: asString(product.entityId),
      appId,
      appName,
      appWebsiteUrl:
        asWebsiteUrl(catalogProduct?.websiteurl) ?? matrix?.websiteUrl,
      appInCatalog: Boolean(appId && catalogByKey.has(appId)),
      matchStatus: rowStatus,
      storeId: asString(product.storeId),
      provider: asString(product.provider),
      visibility: asString(product.visibility),
      shortDescription: asString(product.shortDescription),
      source: "artifact",
    });
  }

  if (artifact.products.length > 0 && rows.length === 0) return undefined;
  if (rows.length === 0 && !complete) return undefined;
  if (skipped > 0) {
    warnings.push(`已跳过 ${skipped} 条缺少 code 或重复的商品记录。`);
  }
  if (expectedCount !== undefined && rows.length !== expectedCount) {
    warnings.push(
      `制品商品数 ${rows.length} 与声明数量 ${expectedCount} 不一致。`,
    );
  }
  if (inventoryErrors > 0) {
    warnings.push(`商品制品包含 ${inventoryErrors} 条采集错误。`);
  }
  if (matching?.complete === false) {
    warnings.push("应用匹配未完成，部分商品可能缺少应用 ID。");
  }

  rows.sort(compareRows);

  return {
    rows,
    complete,
    queriedAt: asString(inventory?.queriedAt),
    storeProvider: asString(inventoryStore?.provider),
    visibility:
      asString(artifact.visibility) ?? asString(inventoryScope?.visibility),
    warnings,
  };
}

function buildArtifactPlatform(
  platform: ProductPlatformConfig,
  parsed: ParsedArtifact,
): PlatformProductData {
  const warnings = [...parsed.warnings];
  if (!parsed.complete) {
    warnings.push("商品制品标记为不完整，列表可能缺少部分商品。");
  }
  if (parsed.queriedAt) {
    const queriedAt = new Date(parsed.queriedAt);
    if (!Number.isNaN(queriedAt.getTime())) {
      const ageHours = (Date.now() - queriedAt.getTime()) / 3_600_000;
      if (ageHours > 48) {
        warnings.push(
          `商品制品数据距今约 ${Math.floor(ageHours / 24)} 天，请确认数据是否需要更新。`,
        );
      }
    }
  }

  const artifactUrl = platform.productsArtifact as string;

  return {
    platform: platform.key,
    label: platform.label,
    description: platform.description,
    stores: platform.stores ?? [],
    searchUrlPrefix: platform.searchUrlPrefix,
    rows: parsed.rows,
    stats: calculateStats(parsed.rows),
    source: "artifact",
    artifact: {
      url: artifactUrl,
      status: parsed.complete ? "complete" : "partial",
      queriedAt: parsed.queriedAt,
      storeProvider: parsed.storeProvider,
      visibility: parsed.visibility,
      warnings,
    },
  };
}

function buildMatrixPlatform(
  platform: ProductPlatformConfig,
  matrix: Map<string, MatrixEntry> | undefined,
  fallback: { status: ArtifactStatus; message: string } | undefined,
): PlatformProductData {
  const rows: PlatformProductRow[] = [];
  for (const entry of matrix?.values() ?? []) {
    rows.push({
      id: `${platform.key}:${normalizeCode(entry.code)}`,
      platform: platform.key,
      title: entry.appName,
      url: asUrl(entry.url),
      code: entry.code,
      appId: entry.appId,
      appName: entry.appName,
      appWebsiteUrl: entry.websiteUrl,
      appInCatalog: entry.appInCatalog,
      matchStatus: "mapped",
      source: "matrix",
    });
  }
  rows.sort(compareRows);

  return {
    platform: platform.key,
    label: platform.label,
    description: platform.description,
    stores: platform.stores ?? [],
    searchUrlPrefix: platform.searchUrlPrefix,
    rows,
    stats: calculateStats(rows),
    source: "matrix",
    artifact: fallback
      ? {
          url: platform.productsArtifact ?? "",
          status: fallback.status,
          warnings: [],
          message:
            rows.length === 0 && fallback.status === "missing"
              ? "该平台未配置 productsArtifact，offer 矩阵也没有该平台的商品映射。"
              : fallback.message,
        }
      : undefined,
  };
}

function buildUnavailablePlatform(
  platform: PlatformConfig,
): PlatformProductData {
  return {
    platform: platform.key,
    label: platform.label,
    description: platform.description,
    stores: platform.stores ?? [],
    searchUrlPrefix: platform.searchUrlPrefix,
    rows: [],
    stats: calculateStats([]),
    source: "unavailable",
    artifact: {
      url: "",
      status: "missing",
      warnings: [],
      message:
        "该平台未配置商品链接模板和 productsArtifact，暂无法生成商品列表。",
    },
  };
}

/**
 * Builds the product-code to application lookup used to enrich generation
 * reports and to fall back to the offer matrix when an artifact is missing.
 */
export function buildMatrixByPlatform(
  marketplace: MarketplaceData,
): Map<PlatformKey, Map<string, MatrixEntry>> {
  const catalogByKey = new Map(
    marketplace.catalog.map((product) => [product.key, product]),
  );
  const matrixByPlatform = new Map<PlatformKey, Map<string, MatrixEntry>>();

  for (const row of marketplace.rows) {
    for (const [platformKey, links] of Object.entries(row.links)) {
      for (const link of links) {
        const codeKey = normalizeCode(link.code);
        if (!codeKey) continue;

        const entries = matrixByPlatform.get(platformKey) ?? new Map();
        if (entries.has(codeKey)) continue;

        const entry: MatrixEntry = {
          appId: row.key,
          appName: catalogName(catalogByKey, row.key) ?? row.name,
          appInCatalog: row.inCatalog,
          websiteUrl: asWebsiteUrl(catalogByKey.get(row.key)?.websiteurl),
          code: link.code,
          url: link.url,
        };
        entries.set(codeKey, entry);
        matrixByPlatform.set(platformKey, entries);
      }
    }
  }

  return matrixByPlatform;
}

export function catalogByKey(
  catalog: CatalogProduct[],
): Map<string, CatalogProduct> {
  return new Map(catalog.map((product) => [product.key, product]));
}

/** Builds one platform view from the offer matrix and an optional artifact. */
export function buildPlatformData(
  platform: PlatformConfig,
  matrixByPlatform: Map<PlatformKey, Map<string, MatrixEntry>>,
  products: Map<string, CatalogProduct>,
  load?: ArtifactLoad,
): PlatformProductData {
  if (!isProductPlatform(platform)) {
    return buildUnavailablePlatform(platform);
  }

  const matrix = matrixByPlatform.get(platform.key);
  const artifactUrl = platform.productsArtifact;

  if (!artifactUrl) {
    return buildMatrixPlatform(platform, matrix, {
      status: "missing",
      message: "该平台未在 config/marketplace.json 中配置 productsArtifact。",
    });
  }

  if (!load || load.error || !load.artifact) {
    return buildMatrixPlatform(platform, matrix, {
      status: "error",
      message: `商品制品加载失败：${load?.error ?? "未知错误"}`,
    });
  }

  const parsed = parseArtifact(
    platform,
    load.artifact,
    matrix ?? new Map(),
    products,
  );
  if (!parsed) {
    return buildMatrixPlatform(platform, matrix, {
      status: "error",
      message: "商品制品不完整或格式无效。",
    });
  }

  return buildArtifactPlatform(platform, parsed);
}

export function buildTotals(
  platforms: PlatformProductData[],
): PlatformProductsTotals {
  return {
    platforms: platforms.length,
    products: platforms.reduce((sum, platform) => sum + platform.stats.total, 0),
    matched: platforms.reduce((sum, platform) => sum + platform.stats.matched, 0),
    mapped: platforms.reduce((sum, platform) => sum + platform.stats.mapped, 0),
    unmatched: platforms.reduce(
      (sum, platform) => sum + platform.stats.unmatched,
      0,
    ),
    apps: new Set(
      platforms.flatMap((platform) =>
        platform.rows.flatMap((row) => (row.appId ? [row.appId] : [])),
      ),
    ).size,
  };
}
