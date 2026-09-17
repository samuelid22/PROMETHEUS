const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL;

/**
 * Keeps local development same-origin while allowing the static production
 * bundle to call a separately deployed API. Only VITE_ values are public.
 */
export function apiUrl(path, baseUrl = configuredBaseUrl) {
  if (!path.startsWith("/")) throw new Error("API paths must start with '/'.");
  const configured = String(baseUrl || "").trim();
  if (!configured) return path;

  const parsed = new URL(configured);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("VITE_API_BASE_URL must be an HTTP(S) URL.");
  }
  return `${parsed.origin}${path}`;
}
