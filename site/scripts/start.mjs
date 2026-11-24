import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import net from "node:net";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const siteRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const defaultPort = 9001;
const maxAttempts = 100;

function parsePort(value) {
  if (value === undefined) return undefined;
  const port = Number(value);
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : undefined;
}

function requestedPort(args) {
  let port = parsePort(process.env.PORT) ?? defaultPort;
  let preview = false;
  const forwarded = [];

  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (arg === "--preview") {
      preview = true;
      continue;
    }
    if (arg === "--port") {
      port = parsePort(args[index + 1]) ?? port;
      index += 1;
      continue;
    }
    if (arg.startsWith("--port=")) {
      port = parsePort(arg.slice("--port=".length)) ?? port;
      continue;
    }
    if (arg === "--background" || arg.startsWith("--background=")) {
      console.warn("Ignoring --background: this command always runs in the foreground.");
      continue;
    }
    forwarded.push(arg);
  }

  return { port, forwarded, preview };
}

function canBind(port, host) {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.unref();
    server.once("error", (error) => {
      resolve(error.code === "EADDRNOTAVAIL" || error.code === "EAFNOSUPPORT");
    });
    server.listen({ port, host, exclusive: true }, () => {
      server.close(() => resolve(true));
    });
  });
}

async function portAvailable(port) {
  const ipv4 = await canBind(port, "0.0.0.0");
  if (!ipv4) return false;
  return canBind(port, "::");
}

async function findPort(startPort) {
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const port = startPort + attempt;
    if (port > 65535) break;
    if (await portAvailable(port)) return port;
  }
  return undefined;
}

const {
  port: preferredPort,
  forwarded,
  preview,
} = requestedPort(process.argv.slice(2));
const port = await findPort(preferredPort);

// Expose the server on the LAN unless the caller passed their own --host.
const hasHostFlag = forwarded.some(
  (arg) => arg === "--host" || arg === "-H" || arg.startsWith("--host="),
);
const hasAllowedHostsFlag = forwarded.some(
  (arg) => arg === "--allowed-hosts" || arg.startsWith("--allowed-hosts="),
);
const hostArgs = [
  ...(hasHostFlag ? [] : ["--host"]),
  ...(hasAllowedHostsFlag ? [] : ["--allowed-hosts"]),
];

if (preview && !existsSync(join(siteRoot, "dist"))) {
  console.error(`No site build found in ${join(siteRoot, "dist")}. Run "npm run build" first.`);
  process.exit(1);
}

if (!port) {
  console.error(
    `Unable to find a free port from ${preferredPort} to ${preferredPort + maxAttempts - 1}.`,
  );
  process.exit(1);
}

const astroBin = join(
  siteRoot,
  "node_modules",
  ".bin",
  process.platform === "win32" ? "astro.cmd" : "astro",
);

if (!existsSync(astroBin)) {
  console.error(`Astro is not installed in ${siteRoot}. Run "npm install" first.`);
  process.exit(1);
}

const mode = preview ? "preview" : "site";
if (port !== preferredPort) {
  console.log(`Port ${preferredPort} is in use; starting the ${mode} on port ${port}.`);
} else {
  console.log(`Starting the ${mode} on port ${port}.`);
}

const child = spawn(
  astroBin,
  [
    preview ? "preview" : "dev",
    "--port",
    String(port),
    "--ignore-lock",
    ...hostArgs,
    ...forwarded,
  ],
  {
    cwd: siteRoot,
    env: process.env,
    stdio: "inherit",
    shell: process.platform === "win32",
  },
);

child.on("error", (error) => {
  console.error(`Unable to start Astro: ${error.message}`);
  process.exit(1);
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    if (!child.killed) child.kill(signal);
  });
}

child.on("exit", (code) => {
  process.exit(code ?? 1);
});
