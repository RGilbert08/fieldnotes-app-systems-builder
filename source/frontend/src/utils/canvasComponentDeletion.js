export function confirmCanvasComponentDeletion(label) {
  const componentLabel = String(label || "canvas component")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 120);

  return window.confirm(
    `Permanently remove "${componentLabel}" from this canvas layout?\n\n` +
      "It will disappear from the preview now. Save and publish the canvas changes " +
      "to persist the removal and broadcast it to connected profiles. This removes " +
      "only the layout block; it does not delete school or learner records.",
  );
}