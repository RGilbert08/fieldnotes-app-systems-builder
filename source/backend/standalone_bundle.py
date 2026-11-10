"""Build offline-friendly, layout-only project bundles in memory."""

from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from backend.canvas_layout import MasterControlLayoutConfiguration
from backend.routes.low_code_websites import (
    SemanticWebsiteBlueprint,
    WEBSITE_ACTION_BINDINGS,
)
from backend.studio_themes import STUDIO_FONT_STACKS, STUDIO_THEMES

CORE_WORKSPACE_EXPORTS = {
    "scholar": {
        "title": "🎓 SCHOLAR PSYCHOMETRICS",
        "description": (
            "CAPS syllabus trackers · 5,000 EC capital runway · "
            "boundaried multi-role channels."
        ),
        "summary": "Scholar workspace overview",
        "color": "#A76E4D",
    },
    "healthcare": {
        "title": "🏥 HEALTHCARE MANAGEMENT",
        "description": (
            "Advanced practice management · protected patient chat · "
            "ICD-10 billing validations."
        ),
        "summary": "Healthcare workspace overview",
        "color": "#557A7B",
    },
}
CORE_WORKSPACE_EXPORT_IDS = frozenset(CORE_WORKSPACE_EXPORTS)


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(maximum, max(minimum, value))


def _clean_canvas_component(component: dict[str, object]) -> dict[str, object]:
    raw_style = component.get("styles")
    style = raw_style if isinstance(raw_style, dict) else {}
    action_binding = component.get("functionBinding")
    action_trigger = component.get("actionTrigger")
    raw_image_settings = component.get("imageSettings")
    image_settings = raw_image_settings if isinstance(raw_image_settings, dict) else {}
    return {
        "id": component.get("id", ""),
        "type": component.get("type", "unknown"),
        "label": component.get("label", ""),
        "content": component.get("content", ""),
        "position": {
            "gridColumn": component.get("gridColumn"),
            "gridRow": component.get("gridRow"),
            "gridOrderIndex": component.get("gridOrderIndex"),
            "gridColumnSpan": component.get("gridColumnSpan", 1),
            "gridRowSpan": component.get("gridRowSpan", 1),
            "x": component.get("x", 0),
            "y": component.get("y", 0),
            "width": component.get("width", 50),
            "height": component.get("height", 24),
        },
        "styles": {
            "padding": style.get("padding", 16),
            "margin": style.get("margin", 0),
            "fontSize": style.get("fontSize", 14),
            "fontFamily": style.get("fontFamily", "system"),
            "fontWeight": style.get("fontWeight", 400),
            "textAlign": style.get("textAlign", "left"),
            "widthPx": style.get("widthPx"),
            "heightPx": style.get("heightPx"),
            "color": style.get("color", "#302B27"),
            "backgroundColor": style.get("backgroundColor", "#FFFFFF"),
            "borderColor": style.get("borderColor", "#D8D0C5"),
            "borderRadius": style.get("borderRadius", 12),
        },
        "imageSettings": {
            "dataUrl": image_settings.get("dataUrl", ""),
            "sourceUrl": image_settings.get("sourceUrl", ""),
            "alt": image_settings.get("alt", ""),
        },
        "menuItems": [
            {
                "id": item.get("id", ""),
                "label": item.get("label", ""),
                "targetTabId": item.get("targetTabId", ""),
            }
            for item in component.get("menuItems", [])
            if isinstance(item, dict)
        ],
        "actionTokens": {
            "binding": action_binding or "",
            "trigger": action_trigger or "",
        },
    }


def _canvas_blueprint(snapshot_data: object, title: str) -> dict[str, object]:
    if not isinstance(snapshot_data, dict):
        raise ValueError("The saved Canvas project data is invalid.")
    try:
        layout = MasterControlLayoutConfiguration.model_validate(
            snapshot_data.get("layout")
        ).model_dump(mode="json")
    except Exception as error:
        raise ValueError("The saved Canvas project layout is invalid.") from error

    source_tabs = layout.get("subTabs") or [
        {
            "tabId": "canvas-main",
            "tabLabelDisplayName": title,
            "displayOrderWeight": 0,
            "layoutMode": layout["layoutMode"],
            "gridColumns": layout["gridColumns"],
            "gridRows": layout["gridRows"],
            "gap": layout["gap"],
            "canvasHeight": layout["canvasHeight"],
            "canvasLayout": layout["canvasLayout"],
        }
    ]
    tabs: list[dict[str, object]] = []
    action_bindings: set[str] = set()
    action_triggers: set[str] = set()
    for index, tab in enumerate(source_tabs):
        if not isinstance(tab, dict):
            continue
        components = [
            _clean_canvas_component(component)
            for component in tab.get("canvasLayout", [])
            if isinstance(component, dict)
        ]
        for component in components:
            tokens = component["actionTokens"]
            if tokens["binding"]:
                action_bindings.add(tokens["binding"])
            if tokens["trigger"]:
                action_triggers.add(tokens["trigger"])
        tabs.append(
            {
                "id": tab.get("tabId") or f"canvas-{index + 1}",
                "label": tab.get("tabLabelDisplayName") or f"Canvas {index + 1}",
                "layoutMode": tab.get("layoutMode", layout["layoutMode"]),
                "gridColumns": tab.get("gridColumns", layout["gridColumns"]),
                "gridRows": tab.get("gridRows", layout["gridRows"]),
                "gap": tab.get("gap", layout["gap"]),
                "canvasHeight": tab.get("canvasHeight", layout["canvasHeight"]),
                "components": components,
            }
        )
    if not tabs:
        raise ValueError("The saved Canvas project does not contain a layout.")

    return {
        "schemaVersion": 1,
        "kind": "canvas",
        "title": title,
        "themeId": layout.get("activeThemeId"),
        "activeTabId": layout.get("activeSubTabId") or tabs[0]["id"],
        "tabs": tabs,
        "allowlistTokens": {
            "actionBindings": sorted(action_bindings),
            "actionTriggers": sorted(action_triggers),
        },
    }


def _website_blueprint(snapshot_data: object, title: str) -> dict[str, object]:
    try:
        parsed = SemanticWebsiteBlueprint.model_validate(snapshot_data)
    except Exception as error:
        raise ValueError("The saved website layout is invalid.") from error

    data = parsed.model_dump(mode="json")
    action_bindings: set[str] = set()

    def clean_sections(source_sections: list[dict[str, object]]) -> list[dict[str, object]]:
        sections = []
        for section in source_sections:
            components = []
            for component in section["children"]:
                action = component["actionBinding"]
                if action in WEBSITE_ACTION_BINDINGS:
                    action_bindings.add(action)
                components.append(
                    {
                        "id": component["id"],
                        "type": component["componentType"],
                        "x": component["x"],
                        "y": component["y"],
                        "width": component["width"],
                        "height": component["height"],
                        "data": component["data"],
                        "tag": component["semanticTag"],
                        "label": component["label"],
                        "content": component["content"],
                        "href": component["href"],
                        "mediaUrl": component["mediaUrl"],
                        "imageUrl": component["imageUrl"],
                        "imageMode": component["imageMode"],
                        "imageAlt": component["imageAlt"],
                        "actionBinding": action,
                        "graphicVariant": component["graphicVariant"],
                        "menuItems": component["menuItems"],
                        "styles": component["styles"],
                    }
                )
            sections.append(
                {
                    "id": section["id"],
                    "tag": section["semanticTag"],
                    "label": section["label"],
                    "columns": section["columns"],
                    "styles": section["styles"],
                    "components": components,
                }
            )
        return sections

    sections = clean_sections(data["sections"])
    pages = [
        {
            "id": page["id"],
            "title": page["title"],
            "slug": page["slug"],
            "sections": clean_sections(page["sections"]),
        }
        for page in data["pages"]
    ]
    return {
        "schemaVersion": 1,
        "kind": "website",
        "title": title,
        "templateType": data["templateType"],
        "themeId": data["themeId"],
        "activePageId": data["activePageId"],
        "pages": pages,
        "sections": sections,
        "allowlistTokens": {"actionBindings": sorted(action_bindings)},
    }


def _standalone_files(blueprint: dict[str, object]) -> dict[str, str]:
    blueprint_json = json.dumps(
        blueprint,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    blueprint_module = (
        "export const blueprint = JSON.parse("
        f"{json.dumps(blueprint_json, ensure_ascii=True)}"
        ");\n"
    )
    package_manifest = json.dumps(
        {
            "name": "fieldnotes-exported-app",
            "version": "1.0.0",
            "private": True,
            "type": "module",
            "scripts": {
                "dev": "vite --host 0.0.0.0",
                "build": "vite build",
                "preview": "vite preview --host 0.0.0.0",
            },
            "devDependencies": {"vite": "^8.3.0"},
        },
        indent=2,
    )
    index_html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="theme-color" content="#f6f2ed" />
    <title>Independent Fieldnotes App</title>
    <style>
      :root { color-scheme: light; font: 16px/1.5 Inter, ui-sans-serif, system-ui, sans-serif; color: #282622; background: #f6f2ed; }
      * { box-sizing: border-box; }
      body { margin: 0; min-width: 320px; }
      button, input { font: inherit; }
      .app-shell { width: min(1180px, calc(100% - 2rem)); margin: 0 auto; padding: clamp(1.5rem, 5vw, 4rem) 0; }
      .app-header { display: grid; gap: .5rem; margin-bottom: 2rem; }
      .app-kicker { color: #806a59; font-size: .72rem; font-weight: 800; letter-spacing: .13em; text-transform: uppercase; }
      h1 { margin: 0; font-size: clamp(2rem, 5vw, 3.5rem); line-height: 1.05; letter-spacing: -.045em; }
      .app-title { max-width: 48rem; color: #6c655d; }
      .layout-tabs { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1rem; }
      .layout-tabs button { border: 1px solid #c8bdb1; border-radius: 999px; padding: .55rem .9rem; color: #39342f; background: #fff; cursor: pointer; }
      .layout-tabs button[aria-selected="true"] { color: #fff; border-color: #302b27; background: #302b27; }
      .canvas-page { min-height: 24rem; padding: 1rem; border: 1px solid #ded5cb; border-radius: 1.2rem; background: #fffdfa; }
      .canvas-grid { display: grid; align-content: start; }
      .canvas-grid.is-absolute { display: block; position: relative; }
      .canvas-block { min-width: 0; overflow-wrap: anywhere; border: 1px solid rgba(48,43,39,.13); box-shadow: 0 5px 20px rgba(48,43,39,.06); }
      .canvas-block h2 { margin: 0 0 .5rem; font: inherit; font-weight: 750; }
      .website-section { margin: 0 0 1rem; padding: clamp(1rem, 3vw, 2rem); border-radius: 1rem; }
      .website-section > * { min-width: 0; }
      .website-component { margin: .75rem 0; }
      .website-layout-canvas { position: relative; width: 100%; overflow: hidden; }
      .website-text-box { white-space: pre-wrap; overflow-wrap: anywhere; }
       .website-shape { display: grid; place-items: center; overflow: hidden; padding: 0 !important; }
       .website-shape--circle, .website-shape--ellipse { border-radius: 50% !important; }
       .website-shape--triangle { clip-path: polygon(50% 0, 100% 100%, 0 100%); }
       .website-shape--diamond { clip-path: polygon(50% 0, 100% 50%, 50% 100%, 0 50%); }
       .website-shape--star { clip-path: polygon(50% 0, 61% 35%, 98% 35%, 68% 57%, 79% 91%, 50% 70%, 21% 91%, 32% 57%, 2% 35%, 39% 35%); }
       .website-shape--arrow { clip-path: polygon(0 25%, 68% 25%, 68% 0, 100% 50%, 68% 100%, 68% 75%, 0 75%); }
       .website-shape--line { clip-path: polygon(0 44%, 100% 44%, 100% 56%, 0 56%); }
       .website-shape__fill-image { position: absolute; z-index: 0; inset: 0; width: 100%; height: 100%; object-fit: cover; }
       .website-shape__text { position: relative; z-index: 1; max-width: 100%; overflow-wrap: anywhere; }
       .website-shape__custom-svg { position: relative; z-index: 1; display: block; width: 100%; height: 100%; object-fit: contain; }
      .website-table { border-collapse: collapse; table-layout: fixed; }
      .website-table th, .website-table td { border: 1px solid #d8d0c5; padding: .5rem; overflow-wrap: anywhere; text-align: left; }
      .website-table th { background: #f1e7dc; font-weight: 700; }
      .website-component[role="link"] { cursor: pointer; }
      .website-component[role="link"]:focus-visible { outline: 2px solid #a75d43; outline-offset: 3px; }
      .website-component a, .website-component button { display: inline-block; border: 0; border-radius: 999px; padding: .7rem 1rem; color: #fff; background: #302b27; text-decoration: none; cursor: pointer; }
       .website-paragraph--background { isolation: isolate; overflow: hidden; color: #fffaf3 !important; text-shadow: 0 1px 2px rgb(0 0 0 / 55%); }
       .website-paragraph--background::after { position: absolute; z-index: 1; inset: 0; background: linear-gradient(115deg, rgb(30 23 19 / 72%), rgb(30 23 19 / 26%)); content: ""; pointer-events: none; }
       .website-paragraph__background { position: absolute; z-index: 0; inset: 0; }
       .website-paragraph__background img { display: block; width: 100%; height: 100%; object-fit: cover; }
       .website-paragraph__copy { position: relative; z-index: 2; white-space: pre-wrap; }
       .website-paragraph__thumbnail { position: relative; display: block; width: min(100%, 20rem); max-width: 100%; margin: .55rem 0 0; padding: 0; overflow: hidden; border: 0; border-radius: .55rem; background: #30251f; cursor: zoom-in; text-align: left; }
       .website-paragraph__thumbnail img { display: block; width: 100%; max-height: 8rem; object-fit: cover; }
       .website-paragraph__thumbnail span { position: absolute; right: .5rem; bottom: .5rem; padding: .2rem .45rem; border-radius: 999px; background: rgb(35 27 22 / 78%); color: #fffaf3; font-size: .72rem; }
       .website-lightbox { position: fixed; z-index: 10000; inset: 0; display: grid; place-items: center; padding: 1rem; background: rgb(24 19 16 / 86%); }
       .website-lightbox__panel { position: relative; display: grid; justify-items: center; gap: .65rem; max-width: min(76rem, 100%); max-height: 100%; padding: 2.75rem .75rem .75rem; border: 1px solid rgb(255 250 243 / 35%); border-radius: .8rem; background: #241d19; color: #fffaf3; }
       .website-lightbox__panel > img { display: block; max-width: 100%; max-height: 78vh; object-fit: contain; }
       .website-lightbox__close { position: absolute; top: .55rem; right: .55rem; min-height: 2rem; padding: .3rem .65rem; border: 1px solid rgb(255 250 243 / 55%); border-radius: 999px; background: #fffaf3; color: #30251f; cursor: pointer; }
       .website-lightbox__caption { max-width: 72ch; margin: 0; color: #f3e4d7; font-size: .9rem; text-align: center; }
      .website-navigation-menu, .canvas-navigation-menu { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; min-width: 0; max-width: 100%; }
      .website-navigation-menu a, .canvas-navigation-menu a { display: inline-flex; max-width: 100%; overflow-wrap: anywhere; border-radius: .5rem; padding: .55rem .8rem; color: inherit; font-weight: 700; text-decoration: none; }
      .website-navigation-menu a:hover, .canvas-navigation-menu a:hover { background: rgba(48,43,39,.1); }
      .website-video { width: min(100%, 50rem); border-radius: .75rem; }
      .website-graphic { min-height: 6rem; border-radius: 1rem; background: radial-gradient(circle at 35% 45%, #d6b8a2, transparent 28%), linear-gradient(120deg, #e6ded4, #b8c7c3); }
      .newsletter-field { display: flex; flex-wrap: wrap; gap: .5rem; }
      .newsletter-field input { min-width: min(100%, 16rem); border: 1px solid #c8bdb1; border-radius: .5rem; padding: .65rem .8rem; }
      .empty-note { color: #756e66; }
      @media (max-width: 640px) { .app-shell { width: min(100% - 1rem, 1180px); } .canvas-page { padding: .65rem; } }
      @media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
    </style>
  </head>
  <body>
    <main id="app" class="app-shell">
      <p class="empty-note">Loading independent project…</p>
    </main>
    <script type="module" src="./runtime_renderer.js"></script>
  </body>
</html>
"""
    runtime_renderer = r"""import { blueprint } from "./app_blueprint.js";

const root = document.querySelector("#app");
const safeTags = new Set(["header", "nav", "section", "main", "article", "aside", "footer", "form", "h2", "h3", "p", "div", "table", "blockquote", "figure", "video", "button", "a", "input"]);
const safeActions = new Set(["SCROLL_TO_CONTACT_FORM", "OPEN_NEWSLETTER_FORM", "OPEN_EXTERNAL_LINK"]);
const colorPattern = /^#[0-9a-fA-F]{6}$/;
const clamp = (value, min, max, fallback) => Number.isInteger(value) ? Math.min(max, Math.max(min, value)) : fallback;
const safeText = (value) => typeof value === "string" ? value : "";
const studioFontStacks = __STUDIO_FONT_STACKS__;
const boxShadows = {
  none: "none",
  soft: "0 2px 8px rgb(47 33 25 / 18%)",
  medium: "0 8px 22px rgb(47 33 25 / 22%)",
  strong: "0 12px 30px rgb(31 22 17 / 36%)",
};
const textShadows = {
  none: "none",
  soft: "0 1px 3px rgb(24 19 16 / 38%)",
  strong: "0 2px 7px rgb(24 19 16 / 72%)",
};
const safeAssetUrl = (value) =>
  typeof value === "string" &&
  (/^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(value) ||
   /^https:\/\/[^/]+/i.test(value) ||
   (/^\/(?!\/)/.test(value) && !/[\\\u0000-\u001f]/.test(value)));

function safeSvgImageSource(markup) {
  if (typeof markup !== "string" || !markup || markup.length > 20000 ||
      /<!doctype|<!entity|<\?xml-stylesheet/i.test(markup)) return "";
  let parsed;
  try {
    parsed = new DOMParser().parseFromString(markup, "image/svg+xml");
  } catch {
    return "";
  }
  const rootNode = parsed.documentElement;
  if (parsed.querySelector("parsererror") || rootNode?.localName !== "svg" ||
      rootNode.namespaceURI !== "http://www.w3.org/2000/svg") return "";
  const allowedElements = new Set(["svg", "g", "rect", "circle", "ellipse", "line", "polyline", "polygon", "path", "text", "tspan", "defs", "linearGradient", "radialGradient", "stop"]);
  const allowedAttributes = new Set(["xmlns", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-opacity", "fill-opacity", "fill-rule", "clip-rule", "opacity", "transform", "id", "offset", "stop-color", "stop-opacity", "gradientUnits", "gradientTransform", "spreadMethod", "font-family", "font-size", "font-weight", "text-anchor", "dominant-baseline", "dx", "dy"]);
  const elements = [rootNode, ...rootNode.querySelectorAll("*")];
  if (elements.length > 120) return "";
  const ids = new Set();
  for (const element of elements) {
    if (element.namespaceURI !== rootNode.namespaceURI || !allowedElements.has(element.localName)) return "";
    for (const attribute of element.attributes) {
      if (!allowedAttributes.has(attribute.name) || attribute.value.length > 2000 ||
          /[\u0000-\u001f]/.test(attribute.value)) return "";
      if (attribute.name === "id") {
        if (!/^[A-Za-z][A-Za-z0-9_.-]{0,63}$/.test(attribute.value) || ids.has(attribute.value)) return "";
        ids.add(attribute.value);
      }
    }
  }
  for (const element of elements) {
    for (const attribute of element.attributes) {
      const value = attribute.value.trim();
      if (["fill", "stroke", "stop-color"].includes(attribute.name)) {
        const localReference = /^url\(#([A-Za-z][A-Za-z0-9_.-]{0,63})\)$/.exec(value);
        if (localReference ? !ids.has(localReference[1]) : !/^(?:none|currentColor|transparent|#[0-9a-fA-F]{3,8}|[A-Za-z]{1,24})$/.test(value)) return "";
      } else if (attribute.name === "href" || attribute.name === "style" || attribute.name.toLowerCase().startsWith("on")) {
        return "";
      } else if (["d", "points"].includes(attribute.name) && !/^[MmZzLlHhVvCcSsQqTtAa0-9eE.,+\-\s]+$/.test(value)) {
        return "";
      } else if (attribute.name === "viewBox" && !/^[+\-\d.eE,\s]+$/.test(value)) {
        return "";
      }
    }
  }
  const safeMarkup = new XMLSerializer().serializeToString(rootNode);
  return safeMarkup.length <= 20000
    ? `data:image/svg+xml;charset=utf-8,${encodeURIComponent(safeMarkup)}`
    : "";
}

function websiteSectionBookmarkId(section) {
  return safeText(section.id);
}

function isWebsiteContactSection(section) {
  const hints = [section.semanticTag || section.tag || "", section.id || "", section.label || ""]
    .map((value) => String(value).trim().toLowerCase().replace(/[^a-z0-9]+/g, "_"));
  return hints.some((value) => /(^|_)(form|contact)(_|$)/.test(value));
}

function applyStyles(node, styles = {}) {
  if (colorPattern.test(styles.color || "")) node.style.color = styles.color;
  const backgroundMode = ["theme", "color", "transparent", "image"].includes(styles.backgroundMode)
    ? styles.backgroundMode
    : "theme";
  if (styles.backgroundColor === "transparent" || colorPattern.test(styles.backgroundColor || "")) {
    node.style.backgroundColor = styles.backgroundColor;
  }
  if (backgroundMode === "transparent") node.style.backgroundColor = "transparent";
  if (backgroundMode === "color" || backgroundMode === "transparent") {
    node.style.backgroundImage = "none";
  } else if (backgroundMode === "image") {
    const backgroundImage = safeAssetUrl(styles.backgroundImage) ? styles.backgroundImage : "";
    node.style.backgroundImage = backgroundImage ? `url(${JSON.stringify(backgroundImage)})` : "none";
    if (backgroundImage) {
      node.style.backgroundPosition = "center";
      node.style.backgroundSize = "cover";
    }
  }
  if (colorPattern.test(styles.borderColor || "")) node.style.borderColor = styles.borderColor;
  if (Number.isInteger(styles.borderWidth)) {
    node.style.borderWidth = `${clamp(styles.borderWidth, 0, 8, 0)}px`;
    if (["solid", "dashed", "dotted", "double"].includes(styles.borderStyle)) {
      node.style.borderStyle = styles.borderStyle;
    }
  }
  if (Number.isInteger(styles.fontSize)) node.style.fontSize = `${clamp(styles.fontSize, 8, 72, 14)}px`;
  if (Number.isInteger(styles.fontWeight)) node.style.fontWeight = String(clamp(styles.fontWeight, 300, 900, 400));
  if (Number.isFinite(styles.lineHeight)) {
    node.style.lineHeight = String(Math.min(2.2, Math.max(1, styles.lineHeight)));
  }
  if (boxShadows[styles.boxShadow]) node.style.boxShadow = boxShadows[styles.boxShadow];
  if (textShadows[styles.textShadow]) node.style.textShadow = textShadows[styles.textShadow];
  if (Number.isInteger(styles.padding)) node.style.padding = `${clamp(styles.padding, 0, 48, 16)}px`;
  if (Number.isInteger(styles.margin)) node.style.margin = `${clamp(styles.margin, 0, 32, 0)}px`;
  if (Number.isInteger(styles.borderRadius)) node.style.borderRadius = `${clamp(styles.borderRadius, 0, 48, 12)}px`;
  if (studioFontStacks[styles.fontFamily]) node.style.fontFamily = studioFontStacks[styles.fontFamily];
  else if (["system", "serif", "sans-serif", "monospace"].includes(styles.fontFamily)) {
    node.style.fontFamily = styles.fontFamily === "system" ? "ui-sans-serif, system-ui, sans-serif" : styles.fontFamily;
  }
  if (["left", "center", "right"].includes(styles.textAlign)) node.style.textAlign = styles.textAlign;
  if (Number.isInteger(styles.widthPx)) node.style.width = `min(100%, ${clamp(styles.widthPx, 120, 1440, 640)}px)`;
  if (Number.isInteger(styles.heightPx)) node.style.minHeight = `${clamp(styles.heightPx, 80, 1600, 120)}px`;
}

function renderCanvas() {
  const header = document.createElement("header");
  header.className = "app-header";
  const kicker = document.createElement("span");
  kicker.className = "app-kicker";
  kicker.textContent = "Independent Canvas Project";
  const title = document.createElement("h1");
  title.textContent = safeText(blueprint.title);
  header.append(kicker, title);
  root.replaceChildren(header);

  const tabs = Array.isArray(blueprint.tabs) ? blueprint.tabs : [];
  const tabNav = document.createElement("nav");
  tabNav.className = "layout-tabs";
  tabNav.setAttribute("aria-label", "Canvas project pages");
  const pageHost = document.createElement("div");
  pageHost.setAttribute("aria-live", "polite");
  root.append(tabNav, pageHost);

  function selectTab(tabId) {
    const tab = tabs.find((item) => item.id === tabId) || tabs[0];
    if (!tab) return;
    blueprint.activeTabId = tab.id;
    for (const button of tabNav.querySelectorAll("button")) {
      button.setAttribute("aria-selected", String(button.dataset.tabId === tab.id));
    }
    const page = document.createElement("section");
    page.className = "canvas-page";
    if (blueprint.themeId) page.dataset.themeId = safeText(blueprint.themeId);
    const canvas = document.createElement("div");
    canvas.className = `canvas-grid${tab.layoutMode === "absolute" ? " is-absolute" : ""}`;
    canvas.style.minHeight = `${clamp(tab.canvasHeight, 240, 1600, 480)}px`;
    canvas.style.gap = `${clamp(tab.gap, 0, 40, 12)}px`;
    if (tab.layoutMode !== "absolute") {
      canvas.style.gridTemplateColumns = `repeat(${clamp(tab.gridColumns, 1, 12, 3)}, minmax(0, 1fr))`;
      canvas.style.gridAutoRows = "minmax(7rem, auto)";
    }
    for (const component of Array.isArray(tab.components) ? tab.components : []) {
      const block = document.createElement("article");
      block.className = "canvas-block";
      applyStyles(block, component.styles);
      const position = component.position || {};
      if (tab.layoutMode === "absolute") {
        block.style.position = "absolute";
        block.style.left = `${clamp(position.x, 0, 100, 0)}%`;
        block.style.top = `${clamp(position.y, 0, 100, 0)}%`;
        block.style.width = `${clamp(position.width, 1, 100, 50)}%`;
        block.style.minHeight = `${clamp(position.height, 1, 100, 24)}%`;
      } else {
        block.style.gridColumn = `span ${clamp(position.gridColumnSpan, 1, 12, 1)}`;
        block.style.gridRow = `span ${clamp(position.gridRowSpan, 1, 30, 1)}`;
        if (Number.isInteger(position.gridOrderIndex)) block.style.order = String(clamp(position.gridOrderIndex, -1000, 1000, 0));
      }
      const heading = document.createElement("h2");
      heading.textContent = safeText(component.label);
      const content = document.createElement("p");
      content.textContent = safeText(component.content);
      block.append(heading);
      if (component.type === "navigation_link_menu_bar") {
        const menu = document.createElement("nav");
        menu.className = "canvas-navigation-menu";
        menu.setAttribute("aria-label", safeText(component.label || "Navigation link menu"));
        for (const item of Array.isArray(component.menuItems) ? component.menuItems : []) {
          const destination = tabs.find((candidate) => candidate.id === item.targetTabId);
          if (!destination) continue;
          const link = document.createElement("a");
          link.href = `#/tab/${encodeURIComponent(destination.id)}`;
          link.textContent = safeText(item.label || destination.label);
          link.addEventListener("click", (event) => {
            event.preventDefault();
            selectTab(destination.id);
          });
          menu.append(link);
        }
        block.append(menu);
      } else {
        block.append(content);
      }
      const imageSettings = component.imageSettings || {};
      const imageUrl = safeAssetUrl(imageSettings.dataUrl)
        ? imageSettings.dataUrl
        : safeAssetUrl(imageSettings.sourceUrl)
          ? imageSettings.sourceUrl
          : "";
      if (imageUrl) {
        const image = document.createElement("img");
        image.src = imageUrl;
        image.alt = safeText(imageSettings.alt);
        image.loading = "lazy";
        image.style.display = "block";
        image.style.maxWidth = "100%";
        image.style.maxHeight = "24rem";
        image.style.objectFit = "cover";
        block.append(image);
      }
      const tokens = component.actionTokens || {};
      if (tokens.binding || tokens.trigger) {
        block.dataset.actionBinding = safeText(tokens.binding);
        block.dataset.actionTrigger = safeText(tokens.trigger);
      }
      canvas.append(block);
    }
    page.append(canvas);
    pageHost.replaceChildren(page);
  }

  for (const tab of tabs) {
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.tabId = safeText(tab.id);
    button.textContent = safeText(tab.label);
    button.addEventListener("click", () => selectTab(tab.id));
    tabNav.append(button);
  }
  selectTab(blueprint.activeTabId);
}

function runWebsiteAction(action, component) {
  if (!safeActions.has(action)) return;
  if (action === "SCROLL_TO_CONTACT_FORM") {
    document.querySelector("form, [data-contact-section]")?.scrollIntoView({ behavior: "smooth", block: "center" });
  } else if (action === "OPEN_NEWSLETTER_FORM") {
    document.querySelector(".newsletter-field input")?.focus();
  } else if (action === "OPEN_EXTERNAL_LINK" && /^https:\/\/[^/]+/i.test(component.href || "")) {
    window.open(component.href, "_blank", "noopener,noreferrer");
  }
}

function resolveWebsiteDestination(value, pages) {
  if (typeof value !== "string" || value !== value.trim()) return null;
  const pageMatch = /^#\/page\/([a-z0-9]+(?:-[a-z0-9]+)*)$/.exec(value);
  const targetPage = pageMatch && pages.find((page) => page.slug === pageMatch[1]);
  if (targetPage) {
    return {
      href: value,
      pageSlug: targetPage.slug,
      targetPageId: targetPage.id,
      external: false,
    };
  }
  if (/^#[A-Za-z][A-Za-z0-9_-]{0,95}$/.test(value)) {
    const anchorId = value.slice(1);
    const target = pages
      .map((page) => ({
        page,
        section: (page.sections || []).find(
          (section) =>
            section.id === anchorId
            || (anchorId === "contact-form" && isWebsiteContactSection(section)),
        ),
      }))
      .find((entry) => entry.section);
    return {
      href: value,
      pageSlug: target?.page.slug || "",
      targetPageId: target?.page.id || "",
      anchorId: target?.section ? websiteSectionBookmarkId(target.section) : "",
      external: false,
    };
  }
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && !url.username && !url.password) {
      return { href: url.href, pageSlug: "", external: true };
    }
  } catch {
    return null;
  }
  return null;
}

function linkifyWebsiteBlock(node, component, pages, activePageId) {
  if (["navigation_link", "cta_button", "navigation_link_menu_bar", "newsletter_input", "inline_video"].includes(component.type)) {
    return node;
  }
  const destination = resolveWebsiteDestination(component.href, pages);
  if (!destination) return node;
  node.setAttribute("role", "link");
  node.setAttribute("tabindex", "0");
  node.setAttribute("aria-label", `Open ${safeText(component.label || "linked block")}`);
  node.style.cursor = "pointer";
  const navigate = (event) => {
    if (event.target !== node && event.target.closest?.("a,button,input,select,textarea,video,audio")) return;
    event.preventDefault();
    if (destination.external) {
      window.open(destination.href, "_blank", "noopener,noreferrer");
    } else if (destination.anchorId) {
      if (destination.pageSlug && destination.targetPageId !== activePageId) {
        navigateWebsitePage(destination.pageSlug);
      }
      window.setTimeout(() => {
        document.getElementById(destination.anchorId)?.scrollIntoView?.({
          behavior: "smooth",
          block: "start",
        });
      }, 0);
    } else if (destination.pageSlug) {
      navigateWebsitePage(destination.pageSlug);
    } else {
      window.location.hash = destination.href;
    }
  };
  node.addEventListener("click", navigate);
  node.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") navigate(event);
  });
  return node;
}

function openWebsiteImageLightbox(imageUrl, imageAlt, trigger) {
  const overlay = document.createElement("div");
  overlay.className = "website-lightbox";
  overlay.setAttribute("role", "dialog");
  overlay.setAttribute("aria-modal", "true");
  overlay.setAttribute("aria-label", `Expanded image: ${safeText(imageAlt) || "Paragraph image"}`);
  const panel = document.createElement("div");
  panel.className = "website-lightbox__panel";
  const closeButton = document.createElement("button");
  closeButton.className = "website-lightbox__close";
  closeButton.type = "button";
  closeButton.textContent = "Close";
  closeButton.setAttribute("aria-label", "Close image");
  const image = document.createElement("img");
  image.src = imageUrl;
  image.alt = safeText(imageAlt);
  const close = () => {
    document.removeEventListener("keydown", closeOnEscape);
    overlay.remove();
    trigger?.focus();
  };
  const closeOnEscape = (event) => {
    if (event.key === "Escape") close();
  };
  closeButton.addEventListener("click", close);
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) close();
  });
  panel.append(closeButton, image);
  if (imageAlt) {
    const caption = document.createElement("p");
    caption.className = "website-lightbox__caption";
    caption.textContent = safeText(imageAlt);
    panel.append(caption);
  }
  overlay.append(panel);
  overlay.addEventListener("keydown", closeOnEscape);
  document.addEventListener("keydown", closeOnEscape);
  document.body.append(overlay);
  closeButton.focus();
}

function renderWebsiteComponent(component, pages = [], activePageId = "", canvasWidth = 1280, canvasHeight = 640) {
  const tag = safeTags.has(component.tag) ? component.tag : "div";
  let node;
  if (component.type === "paragraph" && safeAssetUrl(component.imageUrl)) {
    node = document.createElement("p");
    const imageMode = component.imageMode === "background" ? "background" : "lightbox";
    node.classList.add(`website-paragraph--${imageMode}`);
    const copy = document.createElement("span");
    copy.className = "website-paragraph__copy";
    copy.textContent = safeText(component.content);
    if (imageMode === "background") {
      const background = document.createElement("span");
      background.className = "website-paragraph__background";
      background.setAttribute("aria-hidden", "true");
      const image = document.createElement("img");
      image.src = component.imageUrl;
      image.alt = "";
      image.loading = "lazy";
      background.append(image);
      node.append(background, copy);
    } else {
      const trigger = document.createElement("button");
      trigger.className = "website-paragraph__thumbnail";
      trigger.type = "button";
      trigger.setAttribute(
        "aria-label",
        `View image larger: ${safeText(component.imageAlt) || "Paragraph image"}`,
      );
      const image = document.createElement("img");
      image.src = component.imageUrl;
      image.alt = "";
      image.loading = "lazy";
      const hint = document.createElement("span");
      hint.textContent = "View larger";
      trigger.append(image, hint);
      trigger.addEventListener("click", (event) => {
        event.stopPropagation();
        openWebsiteImageLightbox(component.imageUrl, component.imageAlt, trigger);
      });
      node.append(copy, trigger);
    }
  } else if (component.type === "text_box") {
    node = document.createElement("div");
    node.className = "website-text-box";
    node.textContent = safeText(component.content || component.label);
  } else if (component.type === "table") {
    const rows = safeText(component.content)
      .split(/\r?\n/)
      .map((line) => line.split("|").slice(0, 6).map((cell) => cell.trim().slice(0, 160)))
      .filter((row) => row.some(Boolean))
      .slice(0, 12);
    node = document.createElement("table");
    node.className = "website-table";
    if (rows.length) {
      const heading = document.createElement("thead");
      const headerRow = document.createElement("tr");
      rows[0].forEach((cell) => {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = cell;
        headerRow.append(th);
      });
      heading.append(headerRow);
      node.append(heading);
      if (rows.length > 1) {
        const body = document.createElement("tbody");
        rows.slice(1).forEach((row) => {
          const tr = document.createElement("tr");
          row.forEach((cell) => {
            const td = document.createElement("td");
            td.textContent = cell;
            tr.append(td);
          });
          body.append(tr);
        });
        node.append(body);
      }
    }
  } else if (component.type === "shape") {
    const shapeVariant = [
      "rectangle", "circle", "ellipse", "triangle", "diamond",
      "star", "arrow", "line", "custom_svg",
    ].includes(component.shapeVariant) ? component.shapeVariant : "rectangle";
    node = document.createElement("figure");
    node.className = `website-shape website-shape--${shapeVariant}`;
    if (shapeVariant === "custom_svg") {
      const safeSvg = safeSvgImageSource(component.svgMarkup);
      if (safeSvg) {
        const image = document.createElement("img");
        image.className = "website-shape__custom-svg";
        image.src = safeSvg;
        image.alt = safeText(component.label || "Custom shape");
        node.append(image);
      }
    } else {
      if (safeAssetUrl(component.mediaUrl)) {
        const image = document.createElement("img");
        image.className = "website-shape__fill-image";
        image.src = component.mediaUrl;
        image.alt = "";
        image.loading = "lazy";
        node.append(image);
      }
      if (component.content) {
        const text = document.createElement("span");
        text.className = "website-shape__text";
        text.textContent = safeText(component.content);
        node.append(text);
      }
    }
  } else if (component.type === "inline_video") {
    node = document.createElement("video");
    node.controls = true;
    node.className = "website-video";
    if (/^https:\/\/[^/]+/i.test(component.mediaUrl || "")) node.src = component.mediaUrl;
  } else if (component.type === "svg_graphic") {
    if (safeAssetUrl(component.mediaUrl)) {
      node = document.createElement("img");
      node.src = component.mediaUrl;
      node.alt = safeText(component.label);
      node.loading = "lazy";
    } else {
      node = document.createElement("div");
      node.className = "website-graphic";
      node.setAttribute("role", "img");
      node.setAttribute("aria-label", safeText(component.label));
    }
  } else if (component.type === "hero_banner") {
    node = document.createElement("figure");
    if (safeAssetUrl(component.mediaUrl)) {
      const image = document.createElement("img");
      image.src = component.mediaUrl;
      image.alt = safeText(component.label);
      image.loading = "lazy";
      image.style.display = "block";
      image.style.width = "100%";
      image.style.height = "100%";
      image.style.objectFit = "cover";
      node.append(image);
    }
    const caption = document.createElement("figcaption");
    caption.textContent = safeText(component.content || component.label);
    node.append(caption);
  } else if (component.type === "newsletter_input") {
    node = document.createElement("label");
    node.className = "newsletter-field website-component";
    const text = document.createElement("span");
    text.textContent = safeText(component.content || component.label);
    const input = document.createElement("input");
    input.type = "email";
    input.placeholder = "name@example.com";
    input.autocomplete = "email";
    node.append(text, input);
  } else if (component.type === "navigation_link_menu_bar") {
    node = document.createElement("nav");
    node.className = "website-navigation-menu";
    node.setAttribute("aria-label", safeText(component.label || "Navigation link menu"));
    for (const item of Array.isArray(component.menuItems) ? component.menuItems : []) {
      const targetPage = pages.find((page) => page.slug === item.targetSlug);
      if (!targetPage) continue;
      const link = document.createElement("a");
      link.href = `/${targetPage.slug}`;
      link.textContent = safeText(item.label || targetPage.title);
      if (targetPage.id === activePageId) link.setAttribute("aria-current", "page");
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigateWebsitePage(targetPage.slug);
      });
      node.append(link);
    }
  } else if (component.type === "cta_button" || component.type === "navigation_link") {
    const destination = resolveWebsiteDestination(component.href, pages);
    node = document.createElement(
      component.type === "navigation_link" || destination
        ? "a"
        : "button",
    );
    node.textContent = safeText(component.content || component.label);
    if (destination) {
      node.href = destination.href;
      if (destination.external) {
        node.target = "_blank";
        node.rel = "noopener noreferrer";
      }
    }
    node.addEventListener("click", (event) => {
      if (destination?.anchorId) {
        event.preventDefault();
        if (destination.pageSlug && destination.targetPageId !== activePageId) {
          navigateWebsitePage(destination.pageSlug);
        }
        window.setTimeout(() => {
          document.getElementById(destination.anchorId)?.scrollIntoView?.({
            behavior: "smooth",
            block: "start",
          });
        }, 0);
        return;
      }
      if (destination?.pageSlug) {
        event.preventDefault();
        navigateWebsitePage(destination.pageSlug);
        return;
      }
      if (destination) return;
      if (component.actionBinding !== "OPEN_EXTERNAL_LINK") event.preventDefault();
      runWebsiteAction(component.actionBinding, component);
    });
  } else {
    node = document.createElement(tag);
    node.textContent = safeText(component.content || component.label);
  }
  node.classList.add("website-component");
  applyStyles(node, component.styles);
  if (component.type === "shape") node.style.padding = "0";
  if (component.type === "paragraph" && component.imageMode === "background") {
    node.style.color = "#fffaf3";
  }
  node.style.position = "absolute";
  node.style.left = `${(clamp(component.x, 0, 10000, 0) / Math.max(1, canvasWidth)) * 100}%`;
  node.style.top = `${(clamp(component.y, 0, 10000, 0) / Math.max(1, canvasHeight)) * 100}%`;
  node.style.width = `${(clamp(component.width, 1, 10000, 240) / Math.max(1, canvasWidth)) * 100}%`;
  node.style.height = `${(clamp(component.height, 1, 10000, 140) / Math.max(1, canvasHeight)) * 100}%`;
  node.style.boxSizing = "border-box";
  node.style.margin = "0";
  return linkifyWebsiteBlock(node, component, pages, activePageId);
}

function renderWebsite() {
  const header = document.createElement("header");
  header.className = "app-header";
  const kicker = document.createElement("span");
  kicker.className = "app-kicker";
  kicker.textContent = `Independent Website · ${safeText(blueprint.templateType)}`;
  const title = document.createElement("h1");
  title.textContent = safeText(blueprint.title);
  header.append(kicker, title);
  root.replaceChildren(header);
  const pages = Array.isArray(blueprint.pages) ? blueprint.pages : [];
  const routeMatch = /^#\/page\/([a-z0-9]+(?:-[a-z0-9]+)*)$/.exec(window.location.hash);
  const pathMatch = /^\/([a-z0-9]+(?:-[a-z0-9]+)*)\/?$/.exec(window.location.pathname);
  const activeSlug = routeMatch?.[1] || pathMatch?.[1];
  const activePage = pages.find((page) => page.slug === activeSlug)
    || pages.find((page) => page.id === blueprint.activePageId)
    || pages[0];
  if (pages.length > 1) {
    const navigation = document.createElement("nav");
    navigation.className = "layout-tabs";
    navigation.setAttribute("aria-label", "Website pages");
    for (const page of pages) {
      const link = document.createElement("a");
      link.href = `/${page.slug}`;
      link.textContent = safeText(page.title);
      link.addEventListener("click", (event) => {
        event.preventDefault();
        navigateWebsitePage(page.slug);
      });
      navigation.append(link);
    }
    root.append(navigation);
  }
  const pageSections = activePage?.sections || blueprint.sections;
  for (const section of Array.isArray(pageSections) ? pageSections : []) {
    const tag = safeTags.has(section.tag) ? section.tag : "section";
    const node = document.createElement(tag);
    const publicBookmarkId = websiteSectionBookmarkId(section);
    node.id = /^[A-Za-z][A-Za-z0-9_-]{0,95}$/.test(publicBookmarkId || "")
      ? publicBookmarkId
      : "";
    node.className = "website-section";
    if (/contact/i.test(section.label || "") || tag === "form") node.dataset.contactSection = "true";
    node.style.display = "block";
    const components = Array.isArray(section.components) ? section.components : [];
    const contentBottom = components.reduce(
      (highest, component) =>
        Math.max(
          highest,
          clamp(component.y, 0, 10000, 16)
            + clamp(component.height, 1, 10000, 140),
        ),
      0,
    );
    const canvasWidth = Math.max(
      clamp(section.widthPx, 1, 1440, 1280),
      ...components.map((component) =>
        clamp(component.x, 0, 10000, 0) + clamp(component.width, 1, 10000, 240),
      ),
    );
    const canvasHeight = Math.max(
      clamp(section.heightPx, 1, 2400, 0),
      Math.max(160, contentBottom + 24),
    );
    const canvas = document.createElement("div");
    canvas.className = "website-layout-canvas";
    canvas.style.position = "relative";
    canvas.style.aspectRatio = `${canvasWidth} / ${canvasHeight}`;
    canvas.style.minHeight = "10rem";
    canvas.style.width = "100%";
    if (section.heightPx) canvas.style.height = `${canvasHeight}px`;
    if (section.widthPx) node.style.width = `min(100%, ${canvasWidth + 32}px)`;
    applyStyles(node, section.styles);
    for (const component of components) {
      canvas.append(renderWebsiteComponent(
        component,
        pages,
        activePage?.id || "",
        canvasWidth,
        canvasHeight,
      ));
    }
    node.append(canvas);
    root.append(node);
  }
}

function navigateWebsitePage(slug) {
  if (!Array.isArray(blueprint.pages) || !blueprint.pages.some((page) => page.slug === slug)) {
    return;
  }
  try {
    window.history.pushState({}, "", `/${slug}`);
    renderWebsite();
  } catch {
    window.location.hash = `#/page/${slug}`;
  }
}

if (blueprint.kind === "canvas") renderCanvas();
else if (blueprint.kind === "website") {
  renderWebsite();
  window.addEventListener("hashchange", renderWebsite);
  window.addEventListener("popstate", renderWebsite);
}
else {
  const message = document.createElement("p");
  message.className = "empty-note";
  message.textContent = "This exported layout type is not supported.";
  root.replaceChildren(message);
}
"""
    runtime_renderer = runtime_renderer.replace(
        "__STUDIO_FONT_STACKS__",
        json.dumps(STUDIO_FONT_STACKS, ensure_ascii=True, separators=(",", ":")),
    )
    return {
        "package.json": package_manifest,
        "index.html": index_html,
        "app_blueprint.js": blueprint_module,
        "runtime_renderer.js": runtime_renderer,
    }


def build_canvas_bundle(snapshot_data: object, title: str) -> bytes:
    return _zip_bundle(_canvas_blueprint(snapshot_data, title))


def build_website_bundle(snapshot_data: object, title: str) -> bytes:
    return _zip_bundle(_website_blueprint(snapshot_data, title))


def build_core_workspace_bundle(workspace_id: str) -> bytes:
    workspace = CORE_WORKSPACE_EXPORTS.get(workspace_id)
    if workspace is None:
        raise ValueError("Unsupported core workspace.")
    title = str(workspace["title"])
    description = str(workspace["description"])
    summary = str(workspace["summary"])
    color = str(workspace["color"])
    layout = {
        "schemaVersion": 1,
        "layoutMode": "grid",
        "gridColumns": 2,
        "gridRows": 2,
        "gap": 16,
        "canvasHeight": 480,
        "canvasLayout": [],
        "subTabs": [
            {
                "tabId": "workspace-overview",
                "tabLabelDisplayName": "Workspace Overview",
                "displayOrderWeight": 0,
                "status": "active",
                "layoutMode": "grid",
                "gridColumns": 2,
                "gridRows": 2,
                "gap": 16,
                "canvasHeight": 480,
                "canvasLayout": [
                    {
                        "id": "workspace-title",
                        "type": "text_observation_block",
                        "label": title,
                        "content": description,
                        "gridColumn": 1,
                        "gridRow": 1,
                        "gridColumnSpan": 2,
                        "gridRowSpan": 1,
                        "styles": {
                            "padding": 24,
                            "fontSize": 28,
                            "fontFamily": "serif",
                            "fontWeight": 700,
                            "color": "#F7F3EE",
                            "backgroundColor": color,
                            "borderRadius": 16,
                        },
                    },
                    {
                        "id": "workspace-summary",
                        "type": "text_observation_block",
                        "label": summary,
                        "content": description,
                        "gridColumn": 1,
                        "gridRow": 2,
                        "gridColumnSpan": 2,
                        "gridRowSpan": 1,
                        "styles": {
                            "padding": 20,
                            "fontSize": 16,
                            "fontFamily": "system",
                            "fontWeight": 500,
                            "color": "#302B27",
                            "backgroundColor": "#FFFFFF",
                            "borderRadius": 12,
                        },
                    },
                ],
            }
        ],
        "activeSubTabId": "workspace-overview",
    }
    bundle = _canvas_blueprint({"layout": layout}, title)
    return _zip_bundle(bundle)


def _zip_bundle(blueprint: dict[str, object]) -> bytes:
    files = _standalone_files(blueprint)
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=8) as output:
        for filename, contents in files.items():
            output.writestr(
                f"fieldnotes_exported_app/{filename}",
                contents.encode("utf-8"),
            )
    return archive.getvalue()