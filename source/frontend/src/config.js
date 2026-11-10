import {
  getStoredStudioWebsiteOwner,
  getStoredStudioTenantId,
  STUDIO_WEBSITE_OWNER_HEADER,
  STUDIO_TENANT_HEADER,
} from "./studioTenantContext.js";

const DEFAULT_API_BASE_URL = "http://localhost:8000";
const PRODUCTION_FRONTEND_HOSTS = new Set([
  "fieldnotestech.co.za",
  "www.fieldnotestech.co.za",
  "app.fieldnotestech.co.za",
]);
const INTERNAL_REQUEST_PREFIXES = [
  "/api/",
  "/auth/",
  "/educator/",
  "/parent/",
  "/scholar/",
  "/telemetry/",
];
const inFlightGetRequests = new Map();
const USE_DEVELOPMENT_API_PROXY = Boolean(import.meta.env?.DEV);

export function resolveDefaultApiBaseUrl(
  hostname =
    typeof window !== "undefined" ? window.location.hostname : "localhost",
  protocol =
    typeof window !== "undefined" ? window.location.protocol : "http:",
  isProduction = Boolean(import.meta.env?.PROD),
) {
  const isReplitHost =
    hostname.endsWith(".replit.dev") ||
    hostname.endsWith(".replit.app") ||
    hostname.endsWith(".repl.co");
  if (isReplitHost) {
    return `${protocol}//${hostname}`;
  }
  if (isProduction && hostname.toLowerCase().endsWith(".vercel.app")) {
    return `${protocol}//${hostname}`;
  }
  const isLocalHost = ["localhost", "127.0.0.1", "[::1]"].includes(hostname);
  if (
    isProduction &&
    PRODUCTION_FRONTEND_HOSTS.has(hostname.toLowerCase())
  ) {
    return `${protocol}//${hostname.toLowerCase()}`;
  }
  if (isProduction && !isLocalHost) {
    return "";
  }
  return DEFAULT_API_BASE_URL;
}

export function normalizeApiBaseUrl(
  value,
  isProduction = Boolean(import.meta.env?.PROD),
) {
  const candidate = typeof value === "string" ? value.trim() : "";
  if (!candidate) return "";

  let parsed;
  try {
    parsed = new URL(candidate);
  } catch {
    throw new Error(
      "VITE_API_BASE_URL must be an absolute HTTP(S) backend API URL.",
    );
  }
  if (
    !["http:", "https:"].includes(parsed.protocol) ||
    parsed.username ||
    parsed.password ||
    parsed.search ||
    parsed.hash ||
    (isProduction &&
      ["localhost", "127.0.0.1", "[::1]"].includes(parsed.hostname))
  ) {
    throw new Error(
      "VITE_API_BASE_URL must be a reachable HTTP(S) backend API URL without embedded credentials.",
    );
  }

  return candidate.replace(/\/+$/, "");
}

export function resolveApiBaseUrl(
  hostname =
    typeof window !== "undefined" ? window.location.hostname : "localhost",
  protocol =
    typeof window !== "undefined" ? window.location.protocol : "http:",
  isProduction = Boolean(import.meta.env?.PROD),
  configuredApiBaseUrl = import.meta.env?.VITE_API_BASE_URL,
) {
  const normalizedHostname = String(hostname).toLowerCase();
  const isSameOriginAppHost =
    PRODUCTION_FRONTEND_HOSTS.has(normalizedHostname) ||
    normalizedHostname.endsWith(".vercel.app") ||
    normalizedHostname.endsWith(".replit.dev") ||
    normalizedHostname.endsWith(".replit.app") ||
    normalizedHostname.endsWith(".repl.co");
  const defaultApiBaseUrl = resolveDefaultApiBaseUrl(
    hostname,
    protocol,
    isProduction,
  );
  // These hosts serve their API on the same origin. Prefer that route over a
  // shared VITE_API_BASE_URL so session cookies remain first-party on aliases.
  const selectedApiBaseUrl =
    isProduction && isSameOriginAppHost
      ? defaultApiBaseUrl
      : configuredApiBaseUrl || defaultApiBaseUrl;

  return normalizeApiBaseUrl(selectedApiBaseUrl, isProduction);
}

export const API_BASE_URL = USE_DEVELOPMENT_API_PROXY
  ? ""
  : resolveApiBaseUrl();

export function toApiUrl(requestUrl, apiBaseUrl) {
  if (typeof requestUrl !== "string") return requestUrl;
  if (
    !INTERNAL_REQUEST_PREFIXES.some((prefix) => requestUrl.startsWith(prefix))
  ) {
    return requestUrl;
  }
  const resolvedApiBaseUrl =
    apiBaseUrl === undefined ? API_BASE_URL : apiBaseUrl;
  if (!resolvedApiBaseUrl) {
    if (apiBaseUrl === undefined && USE_DEVELOPMENT_API_PROXY) {
      return requestUrl;
    }
    throw new Error(
      "Set VITE_API_BASE_URL to the backend API origin for this deployment.",
    );
  }
  return `${resolvedApiBaseUrl}${requestUrl}`;
}

export function resolveApiRequestCredentials(
  requestUrl,
  explicitCredentials,
  apiBaseUrl = API_BASE_URL,
) {
  if (explicitCredentials === "omit") return "omit";
  if (!apiBaseUrl) return explicitCredentials || "same-origin";

  try {
    if (new URL(requestUrl).origin === new URL(apiBaseUrl).origin) {
      return "include";
    }
  } catch {
    // Keep the caller's setting for non-URL requests.
  }
  return explicitCredentials || "same-origin";
}

export function installApiBaseUrl() {
  if (typeof window === "undefined" || window.__fieldnotesApiBaseInstalled) {
    return;
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const method = String(
      init?.method || (input instanceof Request ? input.method : "GET"),
    ).toUpperCase();
    const hasAbortSignal = Boolean(
      init?.signal || (input instanceof Request && input.signal),
    );
    const isCoalescible =
      (method === "GET" || method === "HEAD") && !hasAbortSignal;
    const requestUrl =
      input instanceof Request ? input.url : String(input);
    const normalizedUrl = toApiUrl(requestUrl);
    const inheritedCredentials =
      init?.credentials ??
      (input instanceof Request ? input.credentials : undefined);
    const credentials = resolveApiRequestCredentials(
      normalizedUrl,
      inheritedCredentials,
    );
    const requestHeaders = new Headers(
      init?.headers ?? (input instanceof Request ? input.headers : undefined),
    );
    let requestPath = "";
    try {
      requestPath = new URL(normalizedUrl, window.location.href).pathname;
    } catch {
      requestPath = "";
    }
    if (requestPath.startsWith("/api/studio/")) {
      const selectedTenantId = getStoredStudioTenantId();
      if (selectedTenantId && !requestHeaders.has(STUDIO_TENANT_HEADER)) {
        requestHeaders.set(STUDIO_TENANT_HEADER, selectedTenantId);
      }
    }
    if (
      requestPath.startsWith("/api/studio/websites/")
      || requestPath.startsWith("/api/studio/deploy/")
    ) {
      const websiteOwner = getStoredStudioWebsiteOwner();
      if (websiteOwner && !requestHeaders.has(STUDIO_WEBSITE_OWNER_HEADER)) {
        requestHeaders.set(STUDIO_WEBSITE_OWNER_HEADER, websiteOwner);
      }
    }
    const requestInit = { ...init, credentials, headers: requestHeaders };
    const authorization =
      requestHeaders.get("Authorization") || "";
    const studioTenantId =
      requestHeaders.get(STUDIO_TENANT_HEADER) || "";
    const studioWebsiteOwner =
      requestHeaders.get(STUDIO_WEBSITE_OWNER_HEADER) || "";
    const requestKey = isCoalescible
      ? `${method} ${normalizedUrl} ${
          credentials
      } ${authorization} ${studioTenantId} ${studioWebsiteOwner}`
      : null;

    if (requestKey && inFlightGetRequests.has(requestKey)) {
      return inFlightGetRequests.get(requestKey).then((response) => response.clone());
    }

    const request = (() => {
      if (typeof input === "string" || input instanceof URL) {
        return nativeFetch(normalizedUrl, requestInit);
      }
      if (input instanceof Request) {
        return nativeFetch(new Request(normalizedUrl, input), requestInit);
      }
      return nativeFetch(input, requestInit);
    })();

    if (requestKey) {
      const sharedRequest = request.finally(() =>
        inFlightGetRequests.delete(requestKey),
      );
      sharedRequest.catch(() => {});
      inFlightGetRequests.set(
        requestKey,
        sharedRequest,
      );
      return sharedRequest.then((response) => response.clone());
    }

    return request;
  };
  window.__fieldnotesApiBaseInstalled = true;
}