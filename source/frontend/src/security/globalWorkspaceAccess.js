import { MASTER_ADMIN_LOGIN_PATH } from "./loginSpaces";

export const GLOBAL_PROJECT_HUB_PATH = "/global-project-hub";
export const CANVAS_PROJECTS_FOLDER_PATH =
  "/global-project-hub/canvas-projects";
export const LOW_CODE_WEBSITES_FOLDER_PATH =
  "/global-project-hub/low-code-websites";
export const CLOUD_STORAGE_PATH = "/global-project-hub/cloud-storage";
export const RESEARCH_STUDIO_PATH = "/global-project-hub/research-studio";
export const HEALTHCARE_WORKSPACE_PATH = "/healthcare";
export const STUDIO_NEW_PROJECT_PATH = "/studio/new-project";
export const STUDIO_NEW_WEBSITE_PATH = "/studio/new-site";

export function canAccessGlobalWorkspace(user) {
  return (
    user?.role === "MASTER_ADMIN" &&
    user?.can_access_global_project_hub === true
  );
}

export function restrictedWorkspaceFromPath(pathname) {
  const normalizedPath = String(pathname || "/").replace(/\/+$/, "") || "/";
  if (normalizedPath === MASTER_ADMIN_LOGIN_PATH) {
    return "master-admin-dashboard";
  }
  if (normalizedPath === GLOBAL_PROJECT_HUB_PATH) return "hub";
  if (normalizedPath === CANVAS_PROJECTS_FOLDER_PATH) {
    return "canvas-projects-folder";
  }
  if (normalizedPath === LOW_CODE_WEBSITES_FOLDER_PATH) {
    return "websites-folder";
  }
  if (normalizedPath === CLOUD_STORAGE_PATH) return "cloud-storage-folder";
  if (normalizedPath === RESEARCH_STUDIO_PATH) return "research-studio";
  if (normalizedPath === STUDIO_NEW_PROJECT_PATH) return "studio";
  if (normalizedPath === STUDIO_NEW_WEBSITE_PATH) return "website-studio";
  if (
    normalizedPath === HEALTHCARE_WORKSPACE_PATH ||
    normalizedPath === "/healthcare-workspace"
  ) {
    return "healthcare";
  }
  return null;
}

export function clearRestrictedWorkspaceLocation() {
  if (typeof window === "undefined") return;
  window.history.replaceState({}, "", "/");
}