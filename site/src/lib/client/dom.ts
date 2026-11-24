import { clearCache, type ResourceMeta } from "./cache";

const ESCAPES: Record<string, string> = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

/** Markup that is already safe to insert into the page. */
export class SafeHtml {
  constructor(readonly value: string) {}

  toString(): string {
    return this.value;
  }
}

export function escapeHtml(value: unknown): string {
  return String(value).replace(/[&<>"']/g, (character) => ESCAPES[character] ?? character);
}

function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === false || value === true) {
    return "";
  }
  if (value instanceof SafeHtml) return value.value;
  if (Array.isArray(value)) return value.map(renderValue).join("");
  return escapeHtml(value);
}

/**
 * Tagged template that escapes every interpolated value unless it is a
 * `SafeHtml` fragment produced by this module.
 */
export function html(
  strings: TemplateStringsArray,
  ...values: unknown[]
): SafeHtml {
  let output = strings[0] ?? "";
  for (let index = 0; index < values.length; index += 1) {
    output += renderValue(values[index]);
    output += strings[index + 1] ?? "";
  }
  return new SafeHtml(output);
}

export function setContent(
  element: Element | null,
  markup: SafeHtml | string,
): void {
  if (!element) return;
  element.innerHTML = typeof markup === "string" ? markup : markup.value;
}

export function setText(
  root: ParentNode,
  selector: string,
  value: string | number,
): void {
  const element = root.querySelector(selector);
  if (element) element.textContent = String(value);
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Shanghai",
});

export function formatDateTime(epochMs: number): string {
  return dateFormatter.format(new Date(epochMs));
}

export function describeStatus(status: ResourceMeta): string {
  if (!status.fetchedAt) return "正在加载数据…";
  const time = formatDateTime(status.fetchedAt);
  return status.stale
    ? `缓存数据 ${time}，正在后台更新`
    : `数据获取时间 ${time}`;
}

async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  if (!copied) throw new Error("复制失败");
}

const copyTimers = new WeakMap<HTMLButtonElement, number>();

async function copyWithFeedback(
  button: HTMLButtonElement,
  text: string,
): Promise<void> {
  const previousTimer = copyTimers.get(button);
  if (previousTimer) window.clearTimeout(previousTimer);

  const isIcon = button.classList.contains("icon-copy-button");
  const originalLabel = button.getAttribute("aria-label") ?? "";
  const originalTitle = button.title;
  const originalHtml = button.innerHTML;

  try {
    await copyText(text);
    button.classList.add("copied");
    if (isIcon) {
      button.title = "已复制";
      button.setAttribute("aria-label", "已复制");
    } else {
      button.textContent = "已复制";
    }
  } catch {
    button.classList.add("copy-failed");
    if (isIcon) {
      button.title = "复制失败";
      button.setAttribute("aria-label", "复制失败");
    } else {
      button.textContent = "复制失败";
    }
  }

  const timer = window.setTimeout(() => {
    button.classList.remove("copied", "copy-failed");
    if (isIcon) {
      button.title = originalTitle;
      button.setAttribute("aria-label", originalLabel);
    } else {
      button.innerHTML = originalHtml;
    }
  }, 1200);
  copyTimers.set(button, timer);
}

/** Delegated copy handling for every `[data-copy]` button on the page. */
export function wireCopyButtons(): void {
  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>("[data-copy]");
    const text = button?.dataset.copy;
    if (!button || !text) return;
    void copyWithFeedback(button, text);
  });
}

/** Clears cached data and reloads the page. */
export function reloadWithoutCache(): void {
  clearCache();
  location.reload();
}

export function wireRefreshButtons(): void {
  for (const button of document.querySelectorAll<HTMLButtonElement>(
    "[data-refresh]",
  )) {
    button.addEventListener("click", () => reloadWithoutCache());
  }
}

export function showDataError(element: Element | null, message: string): void {
  if (!element) return;
  setContent(
    element,
    html`
      <div>
        <strong>数据加载失败</strong>
        <p>${message}</p>
        <p class="data-alert-hint">
          请检查网络或上游数据源状态；已有的本地缓存会在可以读取时继续使用。
        </p>
      </div>
      <button class="button" type="button" data-retry>重试</button>
    `,
  );
  element.removeAttribute("hidden");
  element.querySelector("[data-retry]")?.addEventListener("click", () => {
    reloadWithoutCache();
  });
}

export function hideDataError(element: Element | null): void {
  element?.setAttribute("hidden", "");
}

export function errorMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const http = /^HTTP (\d+)$/.exec(message);
  if (http) {
    return `请求失败（HTTP ${http[1]}），请检查上游数据源状态或稍后重试。`;
  }
  return message;
}
