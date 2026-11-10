export const FIELDNOTES_COMPASS_PATH = "/compass";
export const FIELDNOTES_CANOPY_PATH = "/canopy";
export const MASTER_ADMIN_LOGIN_PATH = "/admin";
export const FIELDNOTES_APP_LAUNCH_PATH = "/app";

export function isFieldnotesAppLaunchPath(pathname) {
  const normalized = String(pathname || "/").replace(/\/+$/, "") || "/";
  return normalized === FIELDNOTES_APP_LAUNCH_PATH;
}

export function isFieldnotesLoginPath(pathname) {
  const normalized = String(pathname || "/").replace(/\/+$/, "") || "/";
  return [
    FIELDNOTES_COMPASS_PATH,
    FIELDNOTES_CANOPY_PATH,
    MASTER_ADMIN_LOGIN_PATH,
  ].includes(normalized);
}

export function loginSpaceFromPath(pathname) {
  const normalized = String(pathname || "/").replace(/\/+$/, "") || "/";
  if (normalized === FIELDNOTES_COMPASS_PATH) return "compass";
  if (normalized === FIELDNOTES_CANOPY_PATH) return "canopy";
  if (normalized === MASTER_ADMIN_LOGIN_PATH) return "admin";
  return "admin";
}

export function roleMatchesLoginSpace(role, space) {
  if (space === "compass") return role === "SCHOLAR";
  if (space === "canopy") {
    return ["PRINCIPAL", "GRADE_HOD", "EDUCATOR", "PARENT"].includes(role);
  }
  if (space === "admin") return role === "MASTER_ADMIN";
  return true;
}
