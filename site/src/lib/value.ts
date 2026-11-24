export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function asString(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed.length > 0 ? trimmed : undefined;
}

export function asUrl(value: unknown): string | undefined {
  const url = asString(value);
  return url && url.startsWith("https://") ? url : undefined;
}

export function asWebsiteUrl(value: unknown): string | undefined {
  const url = asString(value);
  return url && /^https?:\/\//.test(url) ? url : undefined;
}
