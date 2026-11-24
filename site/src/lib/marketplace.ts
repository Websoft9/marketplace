import {
  OVERVIEW_PLATFORMS,
  PLATFORMS,
  productLink,
  type PlatformKey,
  type StoreConfig,
} from "./config";
import { isRecord } from "./value";

export type {
  PlatformConfig,
  PlatformKey,
  ProductPlatformConfig,
  StoreConfig,
} from "./config";

// Product platforms are intentionally not hardcoded: every entry in
// config/marketplace.json with a urlTemplate becomes a site platform, so new
// clouds (for example huaweiyun and huaweicloud) need no site code change.

export type AppStatus = "listed" | "unlisted" | "outside";

export interface ProductLink {
  platform: PlatformKey;
  code: string;
  url: string;
}

export interface AppRow {
  key: string;
  name: string;
  links: Record<PlatformKey, ProductLink[]>;
  inCatalog: boolean;
  hasLinks: boolean;
}

export interface PlatformCount {
  key: string;
  label: string;
  description: string;
  stores: StoreConfig[];
  count: number;
}

export interface MarketplaceStats {
  catalog: number;
  listed: number;
  unlisted: number;
  outside: number;
  platformCounts: PlatformCount[];
}

export interface MarketplaceData {
  rows: AppRow[];
  stats: MarketplaceStats;
  catalog: CatalogProduct[];
}

export interface Offer {
  id?: string;
  type?: string;
  applications?: string[];
  productID?: Record<string, string[] | undefined>;
}

export interface CatalogCategory {
  key?: string;
  title?: string;
  catalogCollection?: {
    items?: CatalogCategory[];
  };
}

export interface CatalogProduct {
  key: string;
  trademark?: string;
  summary?: string;
  overview?: string;
  description?: string;
  websiteurl?: string;
  keywords?: string[] | string;
  usecases?: string[] | string;
  useCases?: string[] | string;
  distribution?: Array<{ key?: string; value?: string[] }>;
  logo?: { imageurl?: string };
  screenshots?: Array<{ id?: string; key?: string; value?: string }>;
  catalogCollection?: { items?: CatalogCategory[] };
  relatedAppsCollection?: {
    items?: Array<{ key?: string; trademark?: string }>;
  };
}

function mergeOffers(entries: Offer[]): Offer[] {
  const merged: Offer[] = [];
  const byApp = new Map<string, Offer>();
  for (const entry of entries) {
    const app = entry.type === "app" && typeof entry.id === "string" ? entry.id.trim() : "";
    if (!app) {
      merged.push(entry);
      continue;
    }
    const existing = byApp.get(app);
    if (!existing) {
      const copy: Offer = {
        ...entry,
        applications: [...(entry.applications ?? [])],
        productID: Object.fromEntries(
          Object.entries(entry.productID ?? {}).map(([platform, codes]) => [
            platform,
            [...(codes ?? [])],
          ]),
        ),
      };
      merged.push(copy);
      byApp.set(app, copy);
      continue;
    }
    for (const alias of entry.applications ?? []) {
      if (!(existing.applications ?? []).includes(alias)) {
        existing.applications = [...(existing.applications ?? []), alias];
      }
    }
    existing.productID ??= {};
    for (const [platform, codes] of Object.entries(entry.productID ?? {})) {
      const values = existing.productID[platform] ?? [];
      for (const code of codes ?? []) {
        if (!values.includes(code)) values.push(code);
      }
      existing.productID[platform] = values;
    }
  }
  return merged;
}

/**
 * Validates the published offer matrix. The matrix is a JSON array of offers;
 * application offers are merged by application id so split rows still produce a
 * single application entry.
 */
export function parseOffers(value: unknown): Offer[] {
  if (!Array.isArray(value)) {
    throw new Error("商品编码矩阵必须是 JSON 数组。");
  }

  const entries = value.filter(isRecord) as Offer[];
  if (entries.length !== value.length) {
    const skipped = value.length - entries.length;
    console.warn(`[marketplace] 已忽略 ${skipped} 条格式无效的 offer 记录。`);
  }
  return mergeOffers(entries);
}

/** Validates the catalog payload and drops entries without a usable key. */
export function parseCatalog(value: unknown): CatalogProduct[] {
  if (!Array.isArray(value)) {
    throw new Error("应用目录必须是 JSON 数组。");
  }

  return value.filter(
    (item): item is CatalogProduct =>
      isRecord(item) && typeof item.key === "string" && item.key.trim().length > 0,
  );
}

function emptyLinks(): Record<PlatformKey, ProductLink[]> {
  const links = {} as Record<PlatformKey, ProductLink[]>;
  for (const platform of PLATFORMS) {
    links[platform.key] = [];
  }
  return links;
}

export function buildRows(offers: Offer[], catalog: CatalogProduct[]): AppRow[] {
  const catalogByKey = new Map(
    catalog
      .filter((item): item is CatalogProduct & { key: string } => Boolean(item.key))
      .map((item) => [item.key, item]),
  );
  const offerLinks = new Map<string, Record<PlatformKey, ProductLink[]>>();

  for (const offer of offers) {
    if (offer.type !== "app") continue;
    const key = offer.id ?? offer.applications?.[0];
    if (!key) continue;

    const links = offerLinks.get(key) ?? emptyLinks();
    for (const [platformKey, codes] of Object.entries(offer.productID ?? {})) {
      if (!Array.isArray(codes)) continue;
      for (const rawCode of codes) {
        if (!rawCode) continue;
        const link = productLink(platformKey, String(rawCode));
        if (!link) continue;
        const entries = links[link.platform];
        if (!entries.some((entry) => entry.code === link.code && entry.url === link.url)) {
          entries.push(link);
        }
      }
    }
    offerLinks.set(key, links);
  }

  return [...new Set([...catalogByKey.keys(), ...offerLinks.keys()])]
    .map((key) => {
      const catalogItem = catalogByKey.get(key);
      const links = offerLinks.get(key) ?? emptyLinks();
      return {
        key,
        name: catalogItem?.trademark || key,
        links,
        inCatalog: Boolean(catalogItem),
        hasLinks: Object.values(links).some((entries) => entries.length > 0),
      };
    })
    .sort(
      (a, b) =>
        a.name.localeCompare(b.name, undefined, { sensitivity: "base" }) ||
        a.key.localeCompare(b.key),
    );
}

export function rowStatus(row: AppRow): AppStatus {
  if (!row.inCatalog) return "outside";
  return row.hasLinks ? "listed" : "unlisted";
}

export function calculateStats(rows: AppRow[]): MarketplaceStats {
  const catalogRows = rows.filter((row) => row.inCatalog);
  const listed = catalogRows.filter((row) => row.hasLinks);
  const unlisted = catalogRows.filter((row) => !row.hasLinks);
  const outside = rows.filter((row) => !row.inCatalog);

  return {
    catalog: catalogRows.length,
    listed: listed.length,
    unlisted: unlisted.length,
    outside: outside.length,
    platformCounts: OVERVIEW_PLATFORMS.map((platform) => {
      const groupedKeys = PLATFORMS.filter(
        (candidate) => (candidate.overviewKey ?? candidate.key) === platform.key,
      ).map((candidate) => candidate.key);

      return {
        key: platform.key,
        label: platform.label,
        description: platform.description,
        stores: platform.stores ?? [],
        count: listed.filter((row) =>
          groupedKeys.some((key) => (row.links[key] ?? []).length > 0),
        ).length,
      };
    }),
  };
}

export function buildMarketplaceData(
  offers: Offer[],
  catalog: CatalogProduct[],
): MarketplaceData {
  const rows = buildRows(offers, catalog);
  return { rows, stats: calculateStats(rows), catalog };
}
