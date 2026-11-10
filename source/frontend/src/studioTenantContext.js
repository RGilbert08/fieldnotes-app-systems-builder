export const STUDIO_TENANT_HEADER = "X-Fieldnotes-Studio-Tenant";
export const STUDIO_TENANT_SESSION_KEY = "fieldnotes.studioTenantId";
export const STUDIO_WEBSITE_OWNER_HEADER = "X-Fieldnotes-Studio-Website-Owner";
export const STUDIO_WEBSITE_OWNER_SESSION_KEY = "fieldnotes.studioWebsiteOwner";

export function getStoredStudioTenantId() {
  try {
    return window.sessionStorage.getItem(STUDIO_TENANT_SESSION_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredStudioTenantId(tenantId) {
  try {
    if (tenantId) {
      window.sessionStorage.setItem(STUDIO_TENANT_SESSION_KEY, String(tenantId));
    } else {
      window.sessionStorage.removeItem(STUDIO_TENANT_SESSION_KEY);
    }
  } catch {
    // The backend still validates the selected tenant on every request.
  }
}

export function getStoredStudioWebsiteOwner() {
  try {
    return window.sessionStorage.getItem(STUDIO_WEBSITE_OWNER_SESSION_KEY) || "";
  } catch {
    return "";
  }
}

export function setStoredStudioWebsiteOwner(owner) {
  try {
    if (owner === "platform") {
      window.sessionStorage.setItem(STUDIO_WEBSITE_OWNER_SESSION_KEY, owner);
    } else {
      window.sessionStorage.removeItem(STUDIO_WEBSITE_OWNER_SESSION_KEY);
    }
  } catch {
    // The backend still authenticates and validates the selected owner scope.
  }
}

export function getStudioTenantChallenge(payload) {
  const detail = payload?.detail;
  if (
    detail &&
    typeof detail === "object" &&
    ["studio_tenant_selection_required", "studio_no_active_tenants"].includes(
      detail.code,
    )
  ) {
    return detail;
  }
  return null;
}

export function getApiErrorMessage(payload, fallback) {
  if (typeof payload?.detail === "string") return payload.detail;
  if (typeof payload?.detail?.message === "string") {
    return payload.detail.message;
  }
  return fallback;
}
