// Shared row action menu for the product and application tables. The menu
// markup lives in each page; this module only handles opening, positioning,
// per-row link resolution and keyboard interaction.

const ITEM_SELECTOR = 'a[role="menuitem"]';
const MISSING = "\u0000";

export function wireRowActionsMenu(menuElement: HTMLElement | null): void {
  if (!menuElement) return;

  const menu = menuElement;

  let openToggle: HTMLButtonElement | null = null;

  function items(): HTMLAnchorElement[] {
    return Array.from(menu.querySelectorAll<HTMLAnchorElement>(ITEM_SELECTOR));
  }

  function close(options: { focusToggle?: boolean } = {}): void {
    if (!openToggle) return;

    const toggle = openToggle;
    openToggle = null;
    menu.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    if (options.focusToggle) toggle.focus();
  }

  function position(toggle: HTMLButtonElement): void {
    const toggleRect = toggle.getBoundingClientRect();
    const menuRect = menu.getBoundingClientRect();
    let top = toggleRect.bottom + 6;
    if (top + menuRect.height > window.innerHeight - 8) {
      top = toggleRect.top - menuRect.height - 6;
    }
    const left = Math.max(
      12,
      Math.min(
        toggleRect.right - menuRect.width,
        window.innerWidth - menuRect.width - 12,
      ),
    );
    menu.style.top = `${Math.max(12, top)}px`;
    menu.style.left = `${left}px`;
  }

  function resolveLinks(toggle: HTMLButtonElement): void {
    for (const item of items()) {
      const template = item.dataset.hrefTemplate;
      if (template === undefined) {
        item.hidden = false;
        continue;
      }

      const href = template.replace(
        /\{(\w+)\}/g,
        (_match, key: string) => toggle.dataset[key] ?? MISSING,
      );
      const resolved = href.includes(MISSING) ? "" : href;
      item.hidden = resolved.length === 0;
      if (resolved) item.href = resolved;
    }
  }

  function open(toggle: HTMLButtonElement): void {
    if (openToggle === toggle) {
      close();
      return;
    }

    close();
    resolveLinks(toggle);
    openToggle = toggle;
    menu.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    position(toggle);
    items()
      .find((item) => !item.hidden)
      ?.focus();
  }

  document.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;

    const toggle = event.target.closest<HTMLButtonElement>(
      "[data-actions-toggle]",
    );
    if (toggle) {
      event.preventDefault();
      open(toggle);
      return;
    }
    if (!event.target.closest("[data-row-actions-menu]")) {
      close();
    }
  });

  menu.addEventListener("click", (event) => {
    if (event.target instanceof Element && event.target.closest("a")) {
      close();
    }
  });

  menu.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;

    const visible = items().filter((item) => !item.hidden);
    if (visible.length === 0) return;

    const current =
      document.activeElement instanceof HTMLAnchorElement
        ? visible.indexOf(document.activeElement)
        : -1;
    const step = event.key === "ArrowDown" ? 1 : -1;
    const next =
      current < 0 ? 0 : (current + step + visible.length) % visible.length;
    event.preventDefault();
    visible[next]?.focus();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && openToggle) {
      event.preventDefault();
      close({ focusToggle: true });
    }
  });

  window.addEventListener("scroll", () => close(), true);
  window.addEventListener("resize", () => close());
}
