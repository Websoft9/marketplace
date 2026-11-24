import marketplaceConfigJson from "../../../config/marketplace.json";
import sourcesConfigJson from "../../../config/sources.json";

// Build-time configuration. Only platform/source configuration is bundled with
// the site; the data behind these URLs is fetched by the browser at runtime.

export type PlatformKey = string;

export interface StoreConfig {
  label: string;
  url: string;
}

export interface SupplierConfig {
  id: string;
  name: string;
  uid?: string;
}

export interface PlatformConfig {
  key: string;
  label: string;
  description: string;
  urlTemplate?: string;
  searchUrlPrefix?: string;
  stores?: StoreConfig[];
  productsArtifact?: string;
  supplier?: SupplierConfig;
  overviewKey?: string;
}

export interface ProductPlatformConfig extends PlatformConfig {
  key: PlatformKey;
  urlTemplate: string;
}

interface RouteRule {
  prefix: string;
  platform: string;
}

interface PlatformRoute {
  default: string;
  rules?: RouteRule[];
}

interface MarketplaceConfig {
  version: number;
  platforms: PlatformConfig[];
  routing: Record<string, PlatformRoute>;
}

interface SourceConfig {
  summary: string;
  url: string;
}

interface SourcesConfig {
  sources: {
    catalog: SourceConfig;
    versions: SourceConfig;
    category: SourceConfig;
    "offer-codes": SourceConfig;
  };
}

const CONFIG = marketplaceConfigJson as MarketplaceConfig;
const SOURCES = sourcesConfigJson as SourcesConfig;

function assertHttps(value: string, message: string): void {
  if (!value.startsWith("https://")) throw new Error(message);
}

for (const platform of CONFIG.platforms) {
  if (platform.urlTemplate && !platform.urlTemplate.includes("{code}")) {
    throw new Error(`Platform ${platform.key} URL template must include {code}`);
  }
  if (platform.productsArtifact) {
    assertHttps(
      platform.productsArtifact,
      `Platform ${platform.key} product artifact must use HTTPS`,
    );
  }
  if (platform.searchUrlPrefix) {
    assertHttps(
      platform.searchUrlPrefix,
      `Platform ${platform.key} search URL prefix must use HTTPS`,
    );
  }
  for (const store of platform.stores ?? []) {
    assertHttps(store.url, `Platform ${platform.key} store URL must use HTTPS`);
  }
}

export function isProductPlatform(
  platform: PlatformConfig,
): platform is ProductPlatformConfig {
  return typeof platform.urlTemplate === "string";
}

const ALL_PLATFORM_KEYS = new Set(CONFIG.platforms.map((platform) => platform.key));
for (const [source, route] of Object.entries(CONFIG.routing)) {
  if (!ALL_PLATFORM_KEYS.has(route.default)) {
    throw new Error(`Unknown default platform for ${source}: ${route.default}`);
  }
  for (const rule of route.rules ?? []) {
    if (!ALL_PLATFORM_KEYS.has(rule.platform)) {
      throw new Error(`Unknown routed platform for ${source}: ${rule.platform}`);
    }
  }
}

export const ALL_PLATFORMS = CONFIG.platforms;
export const PLATFORMS = CONFIG.platforms.filter(isProductPlatform);
// Platforms that represent the same cloud (for example azure and azure-vmlab)
// are counted once on the overview page.
export const OVERVIEW_PLATFORMS = CONFIG.platforms.filter(
  (platform) => (platform.overviewKey ?? platform.key) === platform.key,
);

export const CATALOG_URL = SOURCES.sources.catalog.url;
export const OFFER_URL = SOURCES.sources["offer-codes"].url;

const PLATFORM_URL_TEMPLATES = Object.fromEntries(
  PLATFORMS.map((platform) => [platform.key, platform.urlTemplate]),
) as Record<PlatformKey, string>;
const PRODUCT_PLATFORM_KEYS = new Set(PLATFORMS.map((platform) => platform.key));

export function isProductPlatformKey(value: string): value is PlatformKey {
  return PRODUCT_PLATFORM_KEYS.has(value);
}

function routePlatform(sourceKey: string, code: string): PlatformKey | undefined {
  const route = CONFIG.routing[sourceKey.toLowerCase()];
  if (!route) return undefined;

  const rule = (route.rules ?? []).find((candidate) =>
    code.toLowerCase().startsWith(candidate.prefix.toLowerCase()),
  );
  const target = rule?.platform ?? route.default;
  return isProductPlatformKey(target) ? target : undefined;
}

export function productLink(
  platformKey: string,
  code: string,
): { platform: PlatformKey; code: string; url: string } | undefined {
  const platform = routePlatform(platformKey, code);
  if (!platform) return undefined;

  const template = PLATFORM_URL_TEMPLATES[platform];
  if (!template) return undefined;
  return { platform, code, url: template.replace(/\{code\}/g, code) };
}
