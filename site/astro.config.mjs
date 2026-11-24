import { fileURLToPath } from "node:url";
import { defineConfig } from "astro/config";

const rawBase = (process.env.BASE_PATH ?? "/").trim();
const base =
  rawBase === "" || rawBase === "/"
    ? "/"
    : `/${rawBase.replace(/^\/+|\/+$/g, "")}/`;

// config/*.json lives one level above the site project and is bundled at build
// time; allow the dev server to serve those files as well.
const siteRoot = fileURLToPath(new URL(".", import.meta.url));
const repoRoot = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig({
  output: "static",
  base,
  site: process.env.SITE_URL || undefined,
  // The dev toolbar adds ~10 extra module requests on every navigation, which
  // is noticeable when the dev server is reached through a slow tunnel such as
  // VS Code port forwarding.
  devToolbar: { enabled: false },
  server: {
    // Expose the dev server on the LAN (and accept any hostname, e.g. when the
    // site is opened through a reverse proxy or a machine name).
    host: true,
    allowedHosts: true,
  },
  vite: {
    server: {
      fs: {
        allow: [siteRoot, repoRoot],
      },
    },
  },
});
