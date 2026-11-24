import { ALL_PLATFORMS, CATALOG_URL, OFFER_URL, isProductPlatform } from "../config";
import {
  buildMarketplaceData,
  parseCatalog,
  parseOffers,
  type CatalogProduct,
  type MarketplaceData,
  type Offer,
} from "../marketplace";
import {
  buildMatrixByPlatform,
  buildPlatformData,
  catalogByKey,
  parseArtifactPayload,
  type ArtifactLoad,
  type GenerationArtifact,
  type PlatformProductData,
} from "../products";
import { loadResource, type ResourceMeta, type ResourceResult } from "./cache";

// The catalog is editorial content and changes rarely; the offer matrix and the
// per-platform generation reports are refreshed more often. Every value is
// revalidated with the upstream ETag, so a long TTL does not hide new data.
const CATALOG_TTL_MS = 6 * 60 * 60 * 1000;
const OFFER_TTL_MS = 60 * 60 * 1000;
const ARTIFACT_TTL_MS = 30 * 60 * 1000;

export interface Loaded<T> {
  data: T;
  status: ResourceMeta;
}

export interface LoadOptions<T> {
  /** Called when a background revalidation produced newer data. */
  onUpdate?: (data: T, status: ResourceMeta) => void;
  /** Called after any successful background revalidation. */
  onRefresh?: (status: ResourceMeta) => void;
}

/**
 * Loads the application catalog and the offer matrix, then derives the
 * application rows and overview statistics. Both sources are cached
 * independently and refreshed in the background when stale.
 */
export async function loadMarketplaceData(
  options: LoadOptions<MarketplaceData> = {},
): Promise<Loaded<MarketplaceData>> {
  let offers: Offer[] | undefined;
  let catalog: CatalogProduct[] | undefined;
  let fetchedAt = 0;

  const publish = () => {
    if (!offers || !catalog) return;
    options.onUpdate?.(buildMarketplaceData(offers, catalog), {
      fetchedAt,
      stale: false,
    });
  };

  const publishRefresh = (meta: ResourceMeta) => {
    fetchedAt = Math.max(fetchedAt, meta.fetchedAt);
    options.onRefresh?.({ fetchedAt, stale: false });
  };

  const [offerResult, catalogResult] = await Promise.all([
    loadResource({
      key: "offers",
      url: OFFER_URL,
      ttlMs: OFFER_TTL_MS,
      parse: parseOffers,
      onRefresh: publishRefresh,
      onUpdate: (data, meta) => {
        offers = data;
        fetchedAt = Math.max(fetchedAt, meta.fetchedAt);
        publish();
      },
    }),
    loadResource({
      key: "catalog",
      url: CATALOG_URL,
      ttlMs: CATALOG_TTL_MS,
      parse: parseCatalog,
      onRefresh: publishRefresh,
      onUpdate: (data, meta) => {
        catalog = data;
        fetchedAt = Math.max(fetchedAt, meta.fetchedAt);
        publish();
      },
    }),
  ]);

  offers = offerResult.data;
  catalog = catalogResult.data;
  fetchedAt = Math.max(offerResult.meta.fetchedAt, catalogResult.meta.fetchedAt);

  return {
    data: buildMarketplaceData(offers, catalog),
    status: {
      fetchedAt,
      stale: offerResult.meta.stale || catalogResult.meta.stale,
    },
  };
}

function describeError(error: unknown): string {
  if (error instanceof Error) {
    const http = /^HTTP (\d+)$/.exec(error.message);
    if (http) return `商品制品请求失败（HTTP ${http[1]}）`;
    return error.message;
  }
  return String(error);
}

/**
 * Loads one platform's product data. The generation report is fetched lazily
 * (the products page starts with the active platform) and falls back to the
 * offer matrix when it cannot be loaded.
 */
export async function loadPlatformProduct(
  platformKey: string,
  marketplace: Promise<Loaded<MarketplaceData>>,
  options: LoadOptions<PlatformProductData> = {},
): Promise<Loaded<PlatformProductData>> {
  const platform = ALL_PLATFORMS.find((candidate) => candidate.key === platformKey);
  if (!platform) throw new Error(`未知平台：${platformKey}`);

  if (!isProductPlatform(platform)) {
    return {
      data: buildPlatformData(platform, new Map(), new Map()),
      status: { fetchedAt: Date.now(), stale: false },
    };
  }

  let latestMarketplace: Loaded<MarketplaceData> | undefined;
  let latestArtifact: ArtifactLoad = { platform };

  const rebuild = (): PlatformProductData | undefined => {
    if (!latestMarketplace) return undefined;
    return buildPlatformData(
      platform,
      buildMatrixByPlatform(latestMarketplace.data),
      catalogByKey(latestMarketplace.data.catalog),
      latestArtifact,
    );
  };

  const artifactPromise: Promise<ResourceResult<GenerationArtifact> | undefined> =
    platform.productsArtifact
      ? loadResource({
          key: `artifact:${platform.key}`,
          url: platform.productsArtifact,
          ttlMs: ARTIFACT_TTL_MS,
          parse: parseArtifactPayload,
          onRefresh: options.onRefresh,
          onUpdate: (data, meta) => {
            latestArtifact = { platform, artifact: data };
            const rebuilt = rebuild();
            if (rebuilt) options.onUpdate?.(rebuilt, meta);
          },
        }).catch((error: unknown) => {
          latestArtifact = { platform, error: describeError(error) };
          return undefined;
        })
      : Promise.resolve(undefined);

  const [marketplaceResult, artifactResult] = await Promise.all([
    marketplace,
    artifactPromise,
  ]);

  latestMarketplace = marketplaceResult;
  if (artifactResult) {
    latestArtifact = { platform, artifact: artifactResult.data };
  }

  const data = rebuild();
  if (!data) throw new Error(`无法生成 ${platform.label} 的商品数据。`);

  return {
    data,
    status: {
      fetchedAt: Math.max(
        marketplaceResult.status.fetchedAt,
        artifactResult?.meta.fetchedAt ?? 0,
      ),
      stale:
        marketplaceResult.status.stale || Boolean(artifactResult?.meta.stale),
    },
  };
}
