import { PLATFORMS } from "../config";
import { asWebsiteUrl } from "../value";
import { html, type SafeHtml } from "./dom";
import type { AppRow, CatalogProduct } from "../marketplace";

function textList(value: string[] | string | undefined): string[] {
  if (Array.isArray(value)) {
    return value.map((item) => item.trim()).filter(Boolean);
  }
  if (typeof value === "string") {
    return value
      .split(/[,;，；]/)
      .map((item) => item.trim())
      .filter(Boolean);
  }
  return [];
}

/**
 * Builds the application detail content shared by the detail page and the
 * application list modal.
 */
export function appDetailMarkup(
  product: CatalogProduct,
  row: AppRow | undefined,
  availableKeys: Set<string>,
  detailHref: (key: string) => string,
): SafeHtml {
  const name = product.trademark ?? product.key;
  const summary = product.summary ?? product.overview ?? "";
  const summaryText = product.summary?.trim() ?? "";
  const logoUrl = asWebsiteUrl(product.logo?.imageurl);
  const overview = product.overview?.trim() ?? "";
  const description = product.description?.trim() ?? "";
  const useCases = textList(product.usecases ?? product.useCases);
  const keywords = textList(product.keywords);
  const categories = (product.catalogCollection?.items ?? [])
    .map((item) => item.title)
    .filter((title): title is string => Boolean(title));
  const versions = Array.from(
    new Set((product.distribution ?? []).flatMap((entry) => entry.value ?? [])),
  ).filter(Boolean);
  const distributions = (product.distribution ?? [])
    .map((entry) => entry.key)
    .filter((entryKey): entryKey is string => Boolean(entryKey));
  const website = asWebsiteUrl(product.websiteurl);
  const relatedApps = (product.relatedAppsCollection?.items ?? []).filter(
    (item): item is { key: string; trademark: string } =>
      Boolean(item.key && item.trademark),
  );
  const platformLinks = row
    ? PLATFORMS.map((platform) => ({
        platform,
        links: row.links[platform.key] ?? [],
      })).filter((entry) => entry.links.length > 0)
    : [];

  return html`
    <header class="page-header detail-header">
      <div class="detail-identity">
        ${logoUrl
          ? html`<img
              class="detail-logo"
              src="${logoUrl}"
              alt="${name} logo"
              loading="lazy"
              referrerpolicy="no-referrer"
            />`
          : html`<span class="detail-logo detail-logo-fallback">${name.slice(0, 2)}</span>`}
        <div>
          <span class="eyebrow">Application / Detail</span>
          <h1>${name}</h1>
          <p class="lede">${summary}</p>
          <div class="detail-links">
            ${categories.map((category) => html`<span class="chip">${category}</span>`)}
            ${website
              ? html`<a class="button" href="${website}" target="_blank" rel="noreferrer">官网</a>`
              : ""}
          </div>
        </div>
      </div>
    </header>

    <div class="detail-grid">
      <div class="detail-main">
        ${summaryText || overview || description
          ? html`
              <section class="panel detail-panel">
                ${summaryText
                  ? html`
                      <div class="detail-section">
                        <h3>Summary</h3>
                        <p class="detail-copy">${summaryText}</p>
                      </div>
                    `
                  : ""}
                ${overview
                  ? html`
                      <div class="detail-section">
                        <h3>Overview</h3>
                        <p class="detail-copy">${overview}</p>
                      </div>
                    `
                  : ""}
                ${description
                  ? html`
                      <div class="detail-section">
                        <h3>Detail</h3>
                        <p class="detail-copy">${description}</p>
                      </div>
                    `
                  : ""}
              </section>
            `
          : ""}

        ${useCases.length > 0
          ? html`
              <section class="panel detail-panel">
                <div class="panel-heading">
                  <div>
                    <h2>Use Cases</h2>
                    <p>适合使用 ${name} 的典型场景。</p>
                  </div>
                </div>
                <p class="detail-copy">${useCases.join(", ")}</p>
              </section>
            `
          : ""}

        ${keywords.length > 0
          ? html`
              <section class="panel detail-panel">
                <div class="panel-heading">
                  <div>
                    <h2>Keywords</h2>
                    <p>Marketplace 搜索关键词。</p>
                  </div>
                </div>
                <div class="chip-list">
                  ${keywords.map((keyword) => html`<span class="chip">${keyword}</span>`)}
                </div>
              </section>
            `
          : ""}
      </div>

      <aside class="detail-side">
        <section class="panel detail-panel">
          <div class="panel-heading">
            <div>
              <h2>详情</h2>
              <p>应用 key、版本和分类。</p>
            </div>
          </div>
          <dl class="detail-facts">
            <div>
              <dt>Key</dt>
              <dd><code>${product.key}</code></dd>
            </div>
            ${versions.length > 0
              ? html`
                  <div>
                    <dt>Versions</dt>
                    <dd>${versions.join(", ")}</dd>
                  </div>
                `
              : ""}
            ${distributions.length > 0
              ? html`
                  <div>
                    <dt>Distribution</dt>
                    <dd>${distributions.join(", ")}</dd>
                  </div>
                `
              : ""}
          </dl>
        </section>

        ${platformLinks.length > 0
          ? html`
              <section class="panel detail-panel">
                <div class="panel-heading">
                  <div>
                    <h2>Marketplace</h2>
                    <p>该应用在各平台的上架链接。</p>
                  </div>
                </div>
                <div class="detail-marketplaces">
                  ${platformLinks.map(
                    ({ platform, links }) => html`
                      <div class="detail-marketplace">
                        <strong>${platform.label}</strong>
                        ${links.map(
                          (link) => html`
                            <a href="${link.url}" target="_blank" rel="noreferrer">${link.code}</a>
                          `,
                        )}
                      </div>
                    `,
                  )}
                </div>
              </section>
            `
          : ""}

        ${relatedApps.length > 0
          ? html`
              <section class="panel detail-panel">
                <div class="panel-heading">
                  <div>
                    <h2>相关应用</h2>
                    <p>同一应用目录中的关联应用。</p>
                  </div>
                </div>
                <div class="chip-list">
                  ${relatedApps.map((app) =>
                    availableKeys.has(app.key)
                      ? html`<a
                          class="chip"
                          data-app-key="${app.key}"
                          href="${detailHref(app.key)}"
                        >${app.trademark}</a>`
                      : html`<span class="chip">${app.trademark}</span>`,
                  )}
                </div>
              </section>
            `
          : ""}
      </aside>
    </div>
  `;
}
