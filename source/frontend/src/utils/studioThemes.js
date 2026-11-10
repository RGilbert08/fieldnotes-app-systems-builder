import themeCatalog from "../../../shared/studio-themes.json";
import websiteFontCatalog from "../../../shared/website-fonts.json";

export const STUDIO_THEMES = themeCatalog.themes;
export const DEFAULT_STUDIO_THEME_ID = "warm-walnut-bone";
export const LEGACY_FONT_KEYS = ["system", "serif", "sans-serif", "monospace"];
export const STUDIO_THEME_IDS = STUDIO_THEMES.map((theme) => theme.id);
export const STUDIO_FONT_KEYS = [
  ...LEGACY_FONT_KEYS,
  ...new Set(STUDIO_THEMES.map((theme) => theme.fontKey)),
];

const SERIF_FONT_KEYS = new Set([
  "serif",
  "playfair-display",
  "merriweather",
  "lora",
  "cinzel",
  "pt-serif",
  "cormorant-garamond",
  "fraunces",
  "eb-garamond",
  "dm-serif-display",
  "libre-baskerville",
  "crimson-pro",
  "source-serif-4",
]);
const MONOSPACE_FONT_KEYS = new Set([
  "monospace",
  "roboto-mono",
  "fira-code",
  "jetbrains-mono",
  "ibm-plex-mono",
  "source-code-pro",
  "space-mono",
  "dm-mono",
]);
const LEGACY_WEBSITE_FONT_OPTIONS = [
  { key: "system", label: "System Sans", category: "Sans serif", fontStack: "system-ui, sans-serif" },
  { key: "sans-serif", label: "Clean Sans", category: "Sans serif", fontStack: "Arial, sans-serif" },
  { key: "serif", label: "Classic Serif", category: "Serif", fontStack: "Georgia, serif" },
  { key: "monospace", label: "Technical Mono", category: "Monospace", fontStack: "ui-monospace, monospace" },
];
const THEME_WEBSITE_FONT_OPTIONS = [
  ...new Map(STUDIO_THEMES.map((theme) => [theme.fontKey, theme])).values(),
].map((theme) => ({
  key: theme.fontKey,
  label: theme.fontFamily,
  category: MONOSPACE_FONT_KEYS.has(theme.fontKey)
    ? "Monospace"
    : SERIF_FONT_KEYS.has(theme.fontKey)
      ? "Serif"
      : "Sans serif",
  fontStack: theme.fontStack,
}));
const EXTRA_WEBSITE_FONT_OPTIONS = websiteFontCatalog.fonts;
const websiteFontOptionsByKey = new Map(
  [
    ...LEGACY_WEBSITE_FONT_OPTIONS,
    ...THEME_WEBSITE_FONT_OPTIONS,
    ...EXTRA_WEBSITE_FONT_OPTIONS,
  ].map((option) => [option.key, option]),
);
export const WEBSITE_FONT_OPTIONS = [...websiteFontOptionsByKey.values()];
export const WEBSITE_FONT_GROUPS = ["Sans serif", "Serif", "Monospace"]
  .map((label) => ({
    label,
    options: WEBSITE_FONT_OPTIONS.filter((option) => option.category === label),
  }))
  .filter((group) => group.options.length > 0);

const THEME_BY_ID = new Map(STUDIO_THEMES.map((theme) => [theme.id, theme]));

export function getStudioTheme(themeId) {
  return THEME_BY_ID.get(themeId) || null;
}

export function getThemedWebsiteSectionStyles(themeId) {
  const theme = getStudioTheme(themeId);
  if (!theme) return null;
  return {
    color: theme.tokens.text,
    backgroundColor: theme.tokens.canvas,
    borderColor: theme.tokens.border,
    fontFamily: theme.fontKey,
  };
}

export function getStudioThemeStyle(themeId = DEFAULT_STUDIO_THEME_ID) {
  const theme = getStudioTheme(themeId) || getStudioTheme(DEFAULT_STUDIO_THEME_ID);
  if (!theme) return {};
  return {
    "--website-canvas": theme.tokens.canvas,
    "--website-surface": theme.tokens.surface,
    "--website-accent": theme.tokens.accent,
    "--website-text": theme.tokens.text,
    "--website-text-on-accent": theme.tokens.textOnAccent,
    "--website-muted": theme.tokens.muted,
    "--website-border": theme.tokens.border,
    "--website-font-family": theme.fontStack,
    backgroundColor: theme.tokens.canvas,
    color: theme.tokens.text,
    fontFamily: theme.fontStack,
  };
}

export function getStudioFontStack(fontKey) {
  return websiteFontOptionsByKey.get(fontKey)?.fontStack || {
    system: "system-ui, sans-serif",
    serif: "Georgia, serif",
    "sans-serif": "Arial, sans-serif",
    monospace: "ui-monospace, monospace",
  }[fontKey] || "system-ui, sans-serif";
}

export function getThemedComponentStyles(themeId, componentType = "") {
  const theme = getStudioTheme(themeId);
  if (!theme) return null;
  const usesAccent = ["cta_button", "hero_banner", "action_button"].includes(
    componentType,
  );
  return {
    color: usesAccent ? theme.tokens.textOnAccent : theme.tokens.text,
    backgroundColor: usesAccent ? theme.tokens.accent : theme.tokens.surface,
    borderColor: theme.tokens.border,
    fontFamily: theme.fontKey,
  };
}

export function applyStudioThemeToComponentTree(components, themeId) {
  const theme = getStudioTheme(themeId);
  if (!theme) {
    throw new Error("Choose one of the supported studio themes.");
  }

  return (Array.isArray(components) ? components : []).map((component) => ({
    ...component,
    styles: {
      ...(component.styles || {}),
      ...getThemedComponentStyles(themeId, component.type),
    },
    children: applyStudioThemeToComponentTree(component.children, themeId),
  }));
}

export function applyStudioThemeToCanvasLayout(layout, themeId) {
  const theme = getStudioTheme(themeId);
  if (!layout || !theme) {
    throw new Error("Choose one of the supported studio themes.");
  }

  const subTabs = Array.isArray(layout.subTabs)
    ? layout.subTabs.map((tab) => ({
        ...tab,
        canvasLayout: applyStudioThemeToComponentTree(tab.canvasLayout, themeId),
      }))
    : [];
  const activeTab = subTabs.find((tab) => tab.tabId === layout.activeSubTabId);
  return {
    ...layout,
    activeThemeId: themeId,
    subTabs,
    canvasLayout: activeTab
      ? activeTab.canvasLayout
      : applyStudioThemeToComponentTree(layout.canvasLayout, themeId),
  };
}