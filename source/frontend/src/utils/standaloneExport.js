const STUDIO_COLLECTIONS = new Set([
  "canvas-projects",
  "semantic-websites",
  "core-workspaces",
]);

export async function downloadStandaloneBundle(collection, itemId) {
  if (!STUDIO_COLLECTIONS.has(collection) || typeof itemId !== "string" || !itemId) {
    throw new Error("Choose a saved Canvas project or website to compile.");
  }

  const response = await fetch(
    `/api/studio/export/standalone/${encodeURIComponent(itemId)}?collection=${encodeURIComponent(collection)}`,
    {
      credentials: "include",
      cache: "no-store",
      headers: { Accept: "application/zip" },
    },
  );
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(
      typeof payload?.detail === "string"
        ? payload.detail
        : "The independent app bundle could not be downloaded.",
    );
  }

  const bundle = await response.blob();
  const objectUrl = URL.createObjectURL(bundle);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = "fieldnotes_exported_app.zip";
  link.style.display = "none";
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
  }
}