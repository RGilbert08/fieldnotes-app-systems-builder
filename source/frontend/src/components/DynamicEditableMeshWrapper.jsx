import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const NATIVE_ELEMENT_SELECTOR = [
  "article",
  "form",
  "table",
  '[role="region"]',
  '[class*="card"]',
  '[class*="panel"]',
  '[class*="chart"]',
  '[class*="metric"]',
  '[class*="tile"]',
  '[class*="widget"]',
  '[class*="console"]',
  '[class*="contract"]',
  '[data-canvas-editable-container="true"]',
].join(",");
const LEAF_ELEMENT_SELECTOR = [
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "p",
  "span",
  "label",
  "button",
  "a",
  "input",
  "textarea",
  "select",
  "small",
  "strong",
  "em",
  "figcaption",
  "legend",
  "summary",
  "li",
  "dt",
  "dd",
  "img",
  "video",
  "svg",
  "svg[aria-label]",
  '[role="img"]',
  '[role="button"]',
  "[aria-label]",
  "[data-canvas-editable-leaf]",
  '[class*="metric"]',
  '[class*="badge"]',
  '[class*="stat"]',
].join(",");

const EDITABLE_HEADING_SELECTOR = "h1,h2,h3,h4,h5,h6";
const EDITABLE_TITLE_DETAIL_SELECTOR = [
  "[data-canvas-editable-title]",
  ".principal-data-kicker",
  ".hod-kicker",
  ".parent-insights-kicker",
].join(",");

const FONT_FAMILY_BY_KEY = {
  system: "system-ui, sans-serif",
  serif: "Georgia, serif",
  "sans-serif": "Arial, sans-serif",
  monospace: "ui-monospace, monospace",
};

const ORIGINAL_ELEMENT_STATE = new WeakMap();
const ORIGINAL_TITLE_STATE = new WeakMap();
const ORIGINAL_LEAF_STATE = new WeakMap();

export const PROFILE_TOOLBOX_DRAG_TYPE = "application/x-fieldnotes-profile-toolbox";
export const PROFILE_COMPONENT_TOOLBOX_ITEMS = [
  {
    id: "heading-h1",
    category: "📝 TYPOGRAPHY",
    label: "H1 Header Row",
    type: "rich_text_block",
    content: "Section heading",
    headingLevel: 1,
  },
  {
    id: "heading-h2",
    category: "📝 TYPOGRAPHY",
    label: "H2 Header Row",
    type: "rich_text_block",
    content: "Section heading",
    headingLevel: 2,
  },
  {
    id: "heading-h3",
    category: "📝 TYPOGRAPHY",
    label: "H3 Header Row",
    type: "rich_text_block",
    content: "Section heading",
    headingLevel: 3,
  },
  {
    id: "paragraph",
    category: "📝 TYPOGRAPHY",
    label: "Paragraph Text",
    type: "text_observation_block",
    content: "Add your paragraph text here.",
  },
  {
    id: "callout",
    category: "📝 TYPOGRAPHY",
    label: "Obsidian Callout",
    type: "text_observation_block",
    content: "Add your alert message.",
    styles: {
      color: "#F4F0E9",
      backgroundColor: "#0A0A0A",
      borderRadius: 12,
    },
  },
  {
    id: "accent-badge",
    category: "📝 TYPOGRAPHY",
    label: "Accent Badge",
    type: "rich_text_block",
    content: "NEW",
    styles: {
      color: "#FFFFFF",
      backgroundColor: "#8F5C38",
      fontSize: 12,
      fontWeight: 700,
      textAlign: "center",
      padding: 8,
      borderRadius: 24,
      widthPx: 120,
      heightPx: 80,
    },
  },
  {
    id: "image",
    category: "🖼️ ADVANCED MEDIA",
    label: "Responsive Image Card",
    type: "image_asset",
    content: "",
  },
  {
    id: "video",
    category: "🖼️ ADVANCED MEDIA",
    label: "Video URL Stream Player",
    type: "video_embed_block",
    content: "",
  },
  {
    id: "svg",
    category: "🖼️ ADVANCED MEDIA",
    label: "SVG Vector Design Code Node",
    type: "svg_graphic_block",
    content: "",
  },
  {
    id: "form-button",
    category: "🔗 NAVIGATION ACTIONS",
    label: "Interactive Action Button",
    type: "custom_button_element",
    content: "",
  },
  {
    id: "hyperlink",
    category: "🔗 NAVIGATION ACTIONS",
    label: "Link Anchor",
    type: "secure_link_anchor",
    content: "Open link",
  },
  {
    id: "navigation-menu-bar",
    category: "🔗 NAVIGATION ACTIONS",
    label: "🔗 Navigation Link Menu Bar",
    type: "navigation_link_menu_bar",
    content: "",
    premium: true,
  },
  {
    id: "master-overview-container",
    category: "🧩 NESTED LAYOUTS",
    label: "🎛️ Master Overview Container Module",
    type: "master_overview_container",
    content: "",
    principalOnly: true,
    styles: {
      padding: 20,
      margin: 0,
      color: "#F8FAFC",
      backgroundColor: "#1F2937",
      borderRadius: 16,
      fontWeight: 500,
    },
  },
];

export function getProfileToolboxItems(profileKey) {
  return PROFILE_COMPONENT_TOOLBOX_ITEMS.filter(
    (item) => !item.principalOnly || profileKey === "principal",
  );
}

const SPECTRUM_SIZE = 220;
const SPECTRUM_CENTER = SPECTRUM_SIZE / 2;
const SPECTRUM_RADIUS = 98;

export function normalizeHexColor(value) {
  const match = String(value || "").trim().match(/^#?([0-9a-f]{3}|[0-9a-f]{6})$/i);
  if (!match) return null;
  const digits = match[1].length === 3
    ? match[1].split("").map((digit) => `${digit}${digit}`).join("")
    : match[1];
  return `#${digits.toUpperCase()}`;
}

function hexToHsl(value) {
  const normalized = normalizeHexColor(value) || "#302B27";
  const red = Number.parseInt(normalized.slice(1, 3), 16) / 255;
  const green = Number.parseInt(normalized.slice(3, 5), 16) / 255;
  const blue = Number.parseInt(normalized.slice(5, 7), 16) / 255;
  const maximum = Math.max(red, green, blue);
  const minimum = Math.min(red, green, blue);
  const delta = maximum - minimum;
  const lightness = (maximum + minimum) / 2;
  let hue = 0;
  let saturation = 0;
  if (delta) {
    saturation = delta / (1 - Math.abs(2 * lightness - 1));
    if (maximum === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (maximum === green) hue = 60 * ((blue - red) / delta + 2);
    else hue = 60 * ((red - green) / delta + 4);
  }
  return {
    hue: (hue + 360) % 360,
    saturation: Math.min(1, Math.max(0, saturation)),
    lightness: Math.min(1, Math.max(0, lightness)),
  };
}

function hslToRgb(hue, saturation, lightness) {
  const normalizedHue = ((hue % 360) + 360) % 360;
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const secondary = chroma * (1 - Math.abs(((normalizedHue / 60) % 2) - 1));
  const offset = lightness - chroma / 2;
  let red = 0;
  let green = 0;
  let blue = 0;
  if (normalizedHue < 60) [red, green, blue] = [chroma, secondary, 0];
  else if (normalizedHue < 120) [red, green, blue] = [secondary, chroma, 0];
  else if (normalizedHue < 180) [red, green, blue] = [0, chroma, secondary];
  else if (normalizedHue < 240) [red, green, blue] = [0, secondary, chroma];
  else if (normalizedHue < 300) [red, green, blue] = [secondary, 0, chroma];
  else [red, green, blue] = [chroma, 0, secondary];
  return [
    Math.round((red + offset) * 255),
    Math.round((green + offset) * 255),
    Math.round((blue + offset) * 255),
  ];
}

function hslToHex(hue, saturation, lightness) {
  return `#${hslToRgb(hue, saturation, lightness)
    .map((channel) => channel.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase()}`;
}

export function NativeSpectrumPicker({ label, value, onChange }) {
  const canvasRef = useRef(null);
  const baseSpectrumRef = useRef(null);
  const color = normalizeHexColor(value) || "#302B27";
  const hsl = hexToHsl(color);

  useEffect(() => {
    const context = canvasRef.current?.getContext("2d");
    if (!context) return;
    if (!baseSpectrumRef.current) {
      const image = context.createImageData(SPECTRUM_SIZE, SPECTRUM_SIZE);
      for (let y = 0; y < SPECTRUM_SIZE; y += 1) {
        for (let x = 0; x < SPECTRUM_SIZE; x += 1) {
          const deltaX = x - SPECTRUM_CENTER;
          const deltaY = y - SPECTRUM_CENTER;
          const distance = Math.hypot(deltaX, deltaY);
          const pixel = (y * SPECTRUM_SIZE + x) * 4;
          if (distance > SPECTRUM_RADIUS) continue;
          const hue = (Math.atan2(deltaY, deltaX) * 180 / Math.PI + 360) % 360;
          const saturation = distance / SPECTRUM_RADIUS;
          const [red, green, blue] = hslToRgb(hue, saturation, 0.5);
          image.data[pixel] = red;
          image.data[pixel + 1] = green;
          image.data[pixel + 2] = blue;
          image.data[pixel + 3] = 255;
        }
      }
      baseSpectrumRef.current = image;
    }
    context.putImageData(baseSpectrumRef.current, 0, 0);
    const crosshairX =
      SPECTRUM_CENTER +
      Math.cos(hsl.hue * Math.PI / 180) * hsl.saturation * SPECTRUM_RADIUS;
    const crosshairY =
      SPECTRUM_CENTER +
      Math.sin(hsl.hue * Math.PI / 180) * hsl.saturation * SPECTRUM_RADIUS;
    context.beginPath();
    context.arc(crosshairX, crosshairY, 7, 0, Math.PI * 2);
    context.lineWidth = 3;
    context.strokeStyle = hsl.lightness > 0.58 ? "#171411" : "#FFFFFF";
    context.stroke();
    context.beginPath();
    context.arc(crosshairX, crosshairY, 9, 0, Math.PI * 2);
    context.lineWidth = 1;
    context.strokeStyle = hsl.lightness > 0.58 ? "#FFFFFF" : "#171411";
    context.stroke();
  }, [color, hsl.hue, hsl.lightness, hsl.saturation]);

  function pickFromPointer(event) {
    const rect = event.currentTarget.getBoundingClientRect();
    const width = rect.width || SPECTRUM_SIZE;
    const height = rect.height || SPECTRUM_SIZE;
    const x = (event.clientX - rect.left) * SPECTRUM_SIZE / width;
    const y = (event.clientY - rect.top) * SPECTRUM_SIZE / height;
    const deltaX = x - SPECTRUM_CENTER;
    const deltaY = y - SPECTRUM_CENTER;
    const hue = (Math.atan2(deltaY, deltaX) * 180 / Math.PI + 360) % 360;
    const saturation = Math.min(
      1,
      Math.hypot(deltaX, deltaY) / SPECTRUM_RADIUS,
    );
    onChange(hslToHex(hue, saturation, hsl.lightness));
  }

  function adjustWithKeyboard(event) {
    const step = event.shiftKey ? 10 : 3;
    let { hue, saturation } = hsl;
    if (event.key === "ArrowRight") hue += step;
    else if (event.key === "ArrowLeft") hue -= step;
    else if (event.key === "ArrowUp") saturation += 0.04;
    else if (event.key === "ArrowDown") saturation -= 0.04;
    else return;
    event.preventDefault();
    onChange(hslToHex(hue, Math.min(1, Math.max(0, saturation)), hsl.lightness));
  }

  return (
    <div className="native-canvas-spectrum">
      <canvas
        aria-label={`${label} spectrum wheel; use arrow keys to adjust hue and saturation`}
        className="native-canvas-spectrum__wheel"
        height={SPECTRUM_SIZE}
        onKeyDown={adjustWithKeyboard}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture?.(event.pointerId);
          pickFromPointer(event);
        }}
        onPointerMove={(event) => {
          if (event.buttons & 1) pickFromPointer(event);
        }}
        role="img"
        tabIndex={0}
        width={SPECTRUM_SIZE}
      />
      <label className="native-canvas-spectrum__lightness">
        Lightness
        <input
          aria-label={`${label} lightness`}
          max={100}
          min={0}
          onChange={(event) =>
            onChange(
              hslToHex(hsl.hue, hsl.saturation, Number(event.target.value) / 100),
            )
          }
          type="range"
          value={Math.round(hsl.lightness * 100)}
        />
      </label>
    </div>
  );
}

function clamp(value, min, max, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

function normalizedNativeOrder(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed >= -1_000 && parsed <= 1_000
    ? parsed
    : null;
}

function normalizedNativeColumnSpan(value) {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 1 && parsed <= 12
    ? parsed
    : null;
}

function slug(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 48) || "block";
}

function directText(element) {
  return Array.from(element.childNodes)
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function leafKindForElement(element) {
  const tagName = element.tagName.toLowerCase();
  if (["input", "textarea", "select"].includes(tagName)) return "form_control";
  if (
    ["button", "a"].includes(tagName) ||
    element.getAttribute("role") === "button"
  ) {
    return "button";
  }
  if (
    ["img", "video", "svg"].includes(tagName) ||
    element.getAttribute("role") === "img"
  ) {
    return "media";
  }
  return "text";
}

function originalAriaLabel(element) {
  if (!ORIGINAL_TITLE_STATE.has(element)) {
    ORIGINAL_TITLE_STATE.set(element, {
      ariaLabel: element.getAttribute("aria-label"),
    });
  }
  return ORIGINAL_TITLE_STATE.get(element).ariaLabel;
}

function originalLeafAttributes(element) {
  if (!ORIGINAL_LEAF_STATE.has(element)) {
    ORIGINAL_LEAF_STATE.set(element, {
      placeholder: element.getAttribute("placeholder"),
      alt: element.getAttribute("alt"),
    });
  }
  return ORIGINAL_LEAF_STATE.get(element);
}

function restoreAttribute(element, name, value) {
  if (value === null) element.removeAttribute(name);
  else element.setAttribute(name, value);
}

function setVisualTextOverride(element, attribute, value) {
  if (value) {
    const color = window.getComputedStyle?.(element)?.color;
    element.style.setProperty(
      "--canvas-native-override-color",
      color || "inherit",
    );
    element.setAttribute(attribute, value);
  } else {
    element.removeAttribute(attribute);
    if (
      !element.hasAttribute("data-canvas-native-title-override") &&
      !element.hasAttribute("data-canvas-native-text-override")
    ) {
      element.style.removeProperty("--canvas-native-override-color");
    }
  }
}

function firstEditableTitle(element) {
  for (const selector of [
    EDITABLE_HEADING_SELECTOR,
    EDITABLE_TITLE_DETAIL_SELECTOR,
  ]) {
    const candidates = element.querySelectorAll(selector);
    for (const candidate of candidates) {
      if (candidate.closest("[data-canvas-editor-ui]")) continue;
      const textNode = Array.from(candidate.childNodes).find(
        (node) => node.nodeType === Node.TEXT_NODE && node.textContent.trim(),
      );
      if (textNode) {
        return {
          element: candidate,
          value: candidate.textContent.trim().slice(0, 120),
        };
      }
    }
  }
  return null;
}

function labelForElement(element) {
  const title = firstEditableTitle(element);
  if (title?.value) return title.value;
  const text = directText(element);
  if (text) return text.slice(0, 120);
  const accessibleName =
    element.getAttribute("aria-label") ||
    element.getAttribute("aria-labelledby")
      ?.split(/\s+/)
      .map((id) => document.getElementById(id)?.textContent || "")
      .join(" ");
  if (accessibleName?.trim()) return accessibleName.trim().slice(0, 120);
  const className =
    typeof element.className === "string" ? element.className : element.tagName;
  return className
    .split(/\s+/)
    .filter((name) => name && !name.startsWith("is-native-canvas-"))
    .join(" ")
    .slice(0, 120) || "Profile element";
}

function discoverNativeElements(root, profileKey) {
  const elements = Array.from(root.querySelectorAll(NATIVE_ELEMENT_SELECTOR))
    .filter((element) => {
      if (
        element.closest("[data-canvas-editor-ui]") ||
        element.matches("button,input,textarea,select")
      ) {
        return false;
      }
      const className =
        typeof element.className === "string" ? element.className : "";
      const stableClassName = className
        .split(/\s+/)
        .filter((name) => name && !name.startsWith("is-native-canvas-"))
        .join(" ");
      const structuralClass =
        /card|panel|chart|tile|widget|console|contract/i.test(stableClassName);
      const explicitShell = element.dataset.canvasEditableContainer === "true";
      const structuralTag =
        element.matches("article,form,table,[role='region']") || explicitShell;
      const textOnly =
        !Array.from(element.children).length && Boolean(directText(element));
      return !textOnly || structuralClass || structuralTag;
    });
  const occurrenceCounts = new Map();
  return elements.map((element, index) => {
    const order =
      normalizedNativeOrder(element.dataset.canvasDefaultGridOrderIndex) ??
      index;
    const gridColumnSpan = normalizedNativeColumnSpan(
      element.dataset.canvasDefaultColumnSpan,
    );
    const label = labelForElement(element);
    const className =
      typeof element.className === "string" ? element.className : element.tagName;
    const primaryClass =
      className
        .split(/\s+/)
        .find((name) => name && !name.startsWith("is-native-canvas-")) ||
      element.tagName;
    const base = `${slug(primaryClass)}-${slug(label)}`;
    const occurrence = occurrenceCounts.get(base) || 0;
    occurrenceCounts.set(base, occurrence + 1);
    const savedId = element.dataset.canvasNativeId;
    const id = savedId?.startsWith(`${profileKey}-dom-`)
      ? savedId
      : element.id
        ? `${profileKey}-dom-${slug(element.id)}`
        : `${profileKey}-dom-${base}-${occurrence + 1}`;
    element.dataset.canvasNativeId = id.slice(0, 160);
    element.dataset.canvasNativeContainer = "true";
    element.dataset.canvasNativeOrder = String(order);
    delete element.dataset.canvasNativeLeaf;
    element.dataset.canvasNativeKind = "container";
    return {
      element,
      id: element.dataset.canvasNativeId,
      label,
      elementKind: "container",
      text: firstEditableTitle(element)?.value || "",
      hasEditableTitle: Boolean(firstEditableTitle(element)),
      gridColumnSpan,
      order,
    };
  });
}

function discoverLeafElements(root, profileKey) {
  const candidates = Array.from(root.querySelectorAll(LEAF_ELEMENT_SELECTOR))
    .filter((element) => {
      if (
        element.closest("[data-canvas-editor-ui]") ||
        element.dataset.canvasNativeContainer === "true"
      ) {
        return false;
      }
      const tagName = element.tagName.toLowerCase();
      if (tagName === "input" && ["hidden", "file", "image"].includes(element.type)) {
        return false;
      }
      return Boolean(
        directText(element) ||
        [
          "button",
          "a",
          "label",
          "input",
          "textarea",
          "select",
          "img",
          "video",
          "svg",
        ].includes(tagName) ||
        element.getAttribute("role") === "button" ||
        element.hasAttribute("data-canvas-editable-leaf") ||
        element.hasAttribute("aria-label") ||
        element.getAttribute("role") === "img",
      );
    });
  const occurrenceCounts = new Map();
  const siblingOrders = new Map();
  return candidates.map((element) => {
    const parent = element.parentElement;
    const index = siblingOrders.get(parent) || 0;
    siblingOrders.set(parent, index + 1);
    element.dataset.canvasNativeOrder = String(index);
    const label = labelForElement(element);
    const tagName = element.tagName.toLowerCase();
    const className =
      typeof element.className === "string" ? element.className : tagName;
    const primaryClass =
      className
        .split(/\s+/)
        .find((name) => name && !name.startsWith("is-native-canvas-")) ||
      tagName;
    const parentScope =
      element.closest('[data-canvas-native-container="true"]')?.dataset
        .canvasNativeId || profileKey;
    const stableHint =
      element.id ||
      element.getAttribute("name") ||
      element.getAttribute("data-testid") ||
      element.getAttribute("aria-label") ||
      "leaf";
    const base = `${slug(parentScope)}-${slug(tagName)}-${slug(primaryClass)}-${slug(stableHint)}`;
    const occurrence = occurrenceCounts.get(base) || 0;
    occurrenceCounts.set(base, occurrence + 1);
    const signature = `${tagName}:${element.id}:${primaryClass}:${stableHint}:${parentScope}`;
    const prefix = `${profileKey}-leaf-`;
    const savedId = element.dataset.canvasNativeId;
    const id =
      savedId?.startsWith(prefix) &&
      element.dataset.canvasNativeSignature === signature
        ? savedId
        : element.id
          ? `${prefix}${slug(tagName)}-${slug(element.id)}`
          : `${prefix}${base}-${occurrence + 1}`;
    element.dataset.canvasNativeId = id.slice(0, 160);
    element.dataset.canvasNativeSignature = signature;
    element.dataset.canvasNativeLeaf = "true";
    const elementKind = leafKindForElement(element);
    element.dataset.canvasNativeKind = elementKind;
    const hasOnlyText =
      !Array.from(element.children).length && Boolean(directText(element));
    const canEditText =
      hasOnlyText && ["text", "button"].includes(elementKind);
    const canEditPlaceholder =
      (tagName === "textarea" || tagName === "input") &&
      ![
        "checkbox",
        "radio",
        "file",
        "hidden",
        "image",
        "submit",
        "button",
        "reset",
      ].includes(element.type);
    const originalAttributes = originalLeafAttributes(element);
    const originalLabel = originalAriaLabel(element);
    return {
      element,
      id: element.dataset.canvasNativeId,
      label,
      elementKind,
      text: canEditText ? directText(element).slice(0, 1_000) : "",
      canEditText,
      canEditPlaceholder,
      placeholder: canEditPlaceholder ? originalAttributes.placeholder || "" : "",
      canEditAlt: tagName === "img",
      alt: tagName === "img" ? originalAttributes.alt || "" : "",
      canEditAriaLabel:
        ["button", "a", "input", "textarea", "select"].includes(tagName) ||
        originalLabel !== null,
      ariaLabel: originalLabel || "",
      hasEditableTitle: false,
      order: index,
    };
  });
}

function NativeNumberField({ label, min, max, value, onCommit }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  return (
    <label className="native-canvas-editor__field">
      {label}
      <input
        aria-label={label}
        max={max}
        min={min}
        onBlur={() => {
          if (draft === "") {
            setDraft(String(value));
            return;
          }
          const next = clamp(draft, min, max, value);
          setDraft(String(next));
          onCommit(next);
        }}
        onChange={(event) => setDraft(event.target.value)}
        type="number"
        value={draft}
      />
    </label>
  );
}

function applyNativeStyle(element, override, canEdit, isSelected) {
  if (!ORIGINAL_ELEMENT_STATE.has(element)) {
    ORIGINAL_ELEMENT_STATE.set(element, {
      draggable: element.getAttribute("draggable"),
      resize: element.style.getPropertyValue("resize"),
      overflow: element.style.getPropertyValue("overflow"),
      order: element.style.getPropertyValue("order"),
      gridColumn: element.style.getPropertyValue("grid-column"),
      gridRow: element.style.getPropertyValue("grid-row"),
      transition: element.style.getPropertyValue("transition"),
    });
  }
  const original = ORIGINAL_ELEMENT_STATE.get(element);
  const styles = override?.styles || {};
  const isLeaf = element.dataset.canvasNativeLeaf === "true";
  const requestedOrderValue = isLeaf
    ? override?.flexOrderWeight ?? override?.order
    : override?.gridOrderIndex ?? override?.order;
  const orderValue =
    normalizedNativeOrder(requestedOrderValue) ??
    normalizedNativeOrder(element.dataset.canvasNativeOrder) ??
    normalizedNativeOrder(original.order);
  const properties = {
    padding: Number.isFinite(styles.padding) ? `${styles.padding}px` : "",
    margin: Number.isFinite(styles.margin) ? `${styles.margin}px` : "",
    color: styles.color || "",
    backgroundColor: styles.backgroundColor || "",
    borderRadius: Number.isFinite(styles.borderRadius)
      ? `${styles.borderRadius}px`
      : "",
    fontSize: Number.isFinite(styles.fontSize) ? `${styles.fontSize}px` : "",
    fontWeight: Number.isFinite(styles.fontWeight)
      ? String(styles.fontWeight)
      : "",
    fontFamily: FONT_FAMILY_BY_KEY[styles.fontFamily] || "",
    textAlign: ["left", "center", "right"].includes(styles.textAlign)
      ? styles.textAlign
      : "",
    width: Number.isFinite(styles.widthPx) ? `${styles.widthPx}px` : "",
    height: Number.isFinite(styles.heightPx) ? `${styles.heightPx}px` : "",
  };
  Object.entries(properties).forEach(([property, value]) => {
    if (value) element.style.setProperty(property.replace(/[A-Z]/g, (char) => `-${char.toLowerCase()}`), value);
  });
  if (orderValue !== null) {
    element.style.setProperty("order", String(orderValue));
  } else {
    element.style.removeProperty("order");
  }
  element.style.setProperty(
    "transition",
    isLeaf ? "all 0.15s ease" : "all 0.2s ease",
  );
  const gridColumnSpan =
    normalizedNativeColumnSpan(override?.gridColumnSpan) ??
    normalizedNativeColumnSpan(element.dataset.canvasDefaultColumnSpan);
  if (gridColumnSpan !== null) {
    element.style.setProperty("grid-column", `span ${gridColumnSpan}`);
  } else if (original.gridColumn) {
    element.style.setProperty("grid-column", original.gridColumn);
  } else {
    element.style.removeProperty("grid-column");
  }
  if (Number.isFinite(override?.gridRowSpan)) {
    element.style.setProperty("grid-row", `span ${override.gridRowSpan}`);
  } else if (original.gridRow) {
    element.style.setProperty("grid-row", original.gridRow);
  } else {
    element.style.removeProperty("grid-row");
  }
  const title = firstEditableTitle(element);
  if (title) {
    const originalLabel = originalAriaLabel(title.element);
    if (override?.labelOverride) {
      setVisualTextOverride(
        title.element,
        "data-canvas-native-title-override",
        override.labelOverride,
      );
      title.element.setAttribute("aria-label", override.labelOverride);
    } else {
      setVisualTextOverride(
        title.element,
        "data-canvas-native-title-override",
        null,
      );
      if (!title.element.dataset.canvasNativeTextOverride) {
        restoreAttribute(title.element, "aria-label", originalLabel);
      }
    }
  }
  if (element.dataset.canvasNativeLeaf === "true") {
    const original = originalLeafAttributes(element);
    if (override?.textOverride) {
      setVisualTextOverride(
        element,
        "data-canvas-native-text-override",
        override.textOverride,
      );
      setVisualTextOverride(
        element,
        "data-canvas-native-title-override",
        null,
      );
    } else {
      setVisualTextOverride(element, "data-canvas-native-text-override", null);
    }
    restoreAttribute(
      element,
      "placeholder",
      override?.placeholderOverride || original.placeholder,
    );
    restoreAttribute(element, "alt", override?.altOverride || original.alt);
    const originalLabel = originalAriaLabel(element);
    if (override?.ariaLabelOverride) {
      element.setAttribute("aria-label", override.ariaLabelOverride);
    } else if (override?.textOverride) {
      element.setAttribute("aria-label", override.textOverride);
    } else if (element.dataset.canvasNativeTitleOverride) {
      element.setAttribute(
        "aria-label",
        element.dataset.canvasNativeTitleOverride,
      );
    } else {
      restoreAttribute(element, "aria-label", originalLabel);
    }
  }
  element.classList.toggle("is-native-canvas-editable", canEdit);
  element.classList.toggle("is-native-canvas-selected", canEdit && isSelected);
  if (canEdit) {
    element.setAttribute("draggable", "true");
    if (!isLeaf && isSelected) {
      element.style.setProperty("resize", "both");
      element.style.setProperty("overflow", "auto");
    } else {
      element.style.removeProperty("resize");
      element.style.removeProperty("overflow");
    }
  } else {
    element.setAttribute("draggable", "false");
    if (original.resize) element.style.setProperty("resize", original.resize);
    else element.style.removeProperty("resize");
    if (original.overflow) element.style.setProperty("overflow", original.overflow);
    else element.style.removeProperty("overflow");
  }
}

function isNativeLeafGroup(group) {
  return group === "leaf";
}

function orderedNativeSiblingEntries(parent, group, elements, overridesById) {
  if (!parent) return [];
  const elementsById = new Map(elements.map((element) => [element.id, element]));
  return Array.from(parent.children)
    .map((node, domIndex) => {
      const id = node.dataset.canvasNativeId;
      const meta = id ? elementsById.get(id) : null;
      const isLeaf = node.dataset.canvasNativeLeaf === "true";
      if (!meta || isLeaf !== isNativeLeafGroup(group)) return null;
      const override = overridesById.get(id) || {};
      const orderValue = isLeaf
        ? override.flexOrderWeight ?? override.order
        : override.gridOrderIndex ?? override.order;
      const normalizedOrder = normalizedNativeOrder(orderValue);
      return {
        domIndex,
        meta,
        orderValue:
          normalizedOrder ??
          normalizedNativeOrder(meta.order) ??
          normalizedNativeOrder(node.dataset.canvasNativeOrder) ??
          0,
      };
    })
    .filter(Boolean)
    .sort(
      (left, right) =>
        left.orderValue - right.orderValue ||
        left.domIndex - right.domIndex,
    );
}

const NATIVE_LEAF_PARENT_LAYOUT_PROPERTIES = [
  "display",
  "flex-direction",
  "flex-wrap",
  "gap",
];

function restoreNativeLeafParentLayout(parent, originalStyles) {
  NATIVE_LEAF_PARENT_LAYOUT_PROPERTIES.forEach((property) => {
    const original = originalStyles[property];
    if (original) parent.style.setProperty(property, original);
    else parent.style.removeProperty(property);
  });
}

function syncNativeLeafParentLayouts(root, elements, overridesById, savedStyles) {
  const leavesByParent = new Map();
  elements
    .filter((element) => element.elementKind !== "container")
    .forEach((element) => {
      const parent = element.element.parentElement;
      if (!parent || !root.contains(parent)) return;
      if (!leavesByParent.has(parent)) leavesByParent.set(parent, []);
      leavesByParent.get(parent).push(element);
    });

  const activeParents = new Set();
  leavesByParent.forEach((siblings, parent) => {
    const hasCustomOrder = siblings.some((element) => {
      const override = overridesById.get(element.id);
      const order = normalizedNativeOrder(
        override?.flexOrderWeight ?? override?.order,
      );
      return order !== null && order !== element.order;
    });
    if (!hasCustomOrder) return;

    activeParents.add(parent);
    if (!savedStyles.has(parent)) {
      savedStyles.set(parent, Object.fromEntries(
        NATIVE_LEAF_PARENT_LAYOUT_PROPERTIES.map((property) => [
          property,
          parent.style.getPropertyValue(property),
        ]),
      ));
    }
    parent.style.setProperty("display", "flex");
    parent.style.setProperty("flex-direction", "row");
    parent.style.setProperty("flex-wrap", "wrap");
    parent.style.setProperty("gap", "1rem");
  });

  savedStyles.forEach((originalStyles, parent) => {
    if (activeParents.has(parent)) return;
    restoreNativeLeafParentLayout(parent, originalStyles);
    savedStyles.delete(parent);
  });
}

function directionalNativeNeighbor(sourceMeta, direction, elements, overridesById) {
  const parent = sourceMeta?.element?.parentElement;
  const group =
    sourceMeta?.element?.dataset.canvasNativeLeaf === "true" ? "leaf" : "container";
  const siblings = orderedNativeSiblingEntries(
    parent,
    group,
    elements,
    overridesById,
  );
  const source = siblings.find(({ meta }) => meta.id === sourceMeta?.id)?.meta;
  if (!source) return null;
  const sourceRect = source.element.getBoundingClientRect();
  const sourceCenterX = sourceRect.left + sourceRect.width / 2;
  const sourceCenterY = sourceRect.top + sourceRect.height / 2;
  const vectors = {
    up: [0, -1],
    down: [0, 1],
    left: [-1, 0],
    right: [1, 0],
  };
  const vector = vectors[direction];
  if (!vector || (!sourceRect.width && !sourceRect.height)) return null;
  const [directionX, directionY] = vector;
  let best = null;
  for (const { meta } of siblings) {
    if (meta.id === source.id) continue;
    const rect = meta.element.getBoundingClientRect();
    if (!rect.width && !rect.height) continue;
    const deltaX = rect.left + rect.width / 2 - sourceCenterX;
    const deltaY = rect.top + rect.height / 2 - sourceCenterY;
    const forwardDistance = deltaX * directionX + deltaY * directionY;
    if (forwardDistance <= 1) continue;
    const crossDistance = Math.abs(
      deltaX * directionY - deltaY * directionX,
    );
    const score = forwardDistance + crossDistance * 1.5;
    if (!best || score < best.score) best = { meta, score };
  }
  return best?.meta || null;
}

export default function DynamicEditableMeshWrapper({
  children,
  className,
  profileKey,
  canEdit,
  onAddToolboxComponent,
  nativeProfileElements,
  selectedElementId,
  onRegisterElements,
  onSelectElement,
  onUpdateElement,
  onReorderElements,
  onResizeElement,
}) {
  const rootRef = useRef(null);
  const dragSourceRef = useRef("");
  const resizeStartRef = useRef(null);
  const toolboxDragRef = useRef(false);
  const currentElementsRef = useRef([]);
  const nativeLeafParentStylesRef = useRef(new Map());
  const overridesById = useMemo(
    () => new Map(nativeProfileElements.map((element) => [element.id, element])),
    [nativeProfileElements],
  );
  const selectedElement = nativeProfileElements.find(
    (element) => element.id === selectedElementId,
  );
  const selectedMeta = currentElementsRef.current.find(
    (element) => element.id === selectedElementId,
  );
  const selectedLabel =
    selectedElement?.textOverride ||
    selectedElement?.labelOverride ||
    selectedElement?.label ||
    selectedMeta?.label ||
    "";
  const selectedDefaultLabel = selectedElement?.label || selectedMeta?.label || "";
  const selectedStyles = selectedElement?.styles || {};
  const selectedKindLabel = {
    container: "Profile container",
    text: "Profile text",
    form_control: "Form control",
    button: "Profile button",
    media: "Profile media",
  }[selectedMeta?.elementKind] || "Profile element";
  const selectedTextValue =
    selectedElement?.textOverride ?? selectedMeta?.text ?? "";
  const selectedPlaceholderValue =
    selectedElement?.placeholderOverride ?? selectedMeta?.placeholder ?? "";
  const selectedAriaLabelValue =
    selectedElement?.ariaLabelOverride ?? selectedMeta?.ariaLabel ?? "";
  const selectedAltValue = selectedElement?.altOverride ?? selectedMeta?.alt ?? "";
  const selectedTextColor =
    normalizeHexColor(selectedStyles.color) || "#302B27";
  const selectedBackgroundColor =
    normalizeHexColor(selectedStyles.backgroundColor) || "#FFFFFF";
  const [labelDraft, setLabelDraft] = useState(selectedLabel);
  const [textDraft, setTextDraft] = useState(selectedTextValue);
  const [placeholderDraft, setPlaceholderDraft] = useState(selectedPlaceholderValue);
  const [ariaLabelDraft, setAriaLabelDraft] = useState(selectedAriaLabelValue);
  const [altDraft, setAltDraft] = useState(selectedAltValue);
  const [textColorDraft, setTextColorDraft] = useState(selectedTextColor);
  const [backgroundColorDraft, setBackgroundColorDraft] = useState(
    selectedBackgroundColor,
  );
  useEffect(() => {
    setLabelDraft(selectedLabel);
  }, [selectedElementId, selectedLabel]);
  useEffect(() => {
    setTextDraft(selectedTextValue);
    setPlaceholderDraft(selectedPlaceholderValue);
    setAriaLabelDraft(selectedAriaLabelValue);
    setAltDraft(selectedAltValue);
  }, [
    selectedElementId,
    selectedTextValue,
    selectedPlaceholderValue,
    selectedAriaLabelValue,
    selectedAltValue,
  ]);
  useEffect(() => {
    setTextColorDraft(selectedTextColor);
    setBackgroundColorDraft(selectedBackgroundColor);
  }, [selectedElementId, selectedTextColor, selectedBackgroundColor]);

  const refreshNativeElements = useCallback(() => {
    const root = rootRef.current;
    if (!root) return;
    const discovered = [
      ...discoverNativeElements(root, profileKey),
      ...discoverLeafElements(root, profileKey),
    ];
    const previousById = new Map(
      currentElementsRef.current.map((element) => [element.id, element]),
    );
    currentElementsRef.current = discovered;
    discovered.forEach(({ id, label, text }) => {
      const previous = previousById.get(id);
      if (
        previous &&
        previous.text !== text &&
        overridesById.get(id)?.textOverride
      ) {
        onUpdateElement(id, {
          label: label || "Profile element",
          textOverride: null,
        });
      }
    });
    onRegisterElements(
      discovered.map(({ id, label, elementKind, order, gridColumnSpan }) => ({
        id,
        label,
        elementKind,
        order,
        ...(Number.isFinite(gridColumnSpan) ? { gridColumnSpan } : {}),
        visibility: {
          master_admin: true,
          principal: true,
          grade_hod: true,
          educator: true,
          parent: true,
          scholar: true,
        },
      })),
    );
    discovered.forEach(({ element, id }) => {
      const override = overridesById.get(id);
      applyNativeStyle(element, override, canEdit, id === selectedElementId);
    });
    syncNativeLeafParentLayouts(
      root,
      discovered,
      overridesById,
      nativeLeafParentStylesRef.current,
    );
  }, [
    canEdit,
    onRegisterElements,
    onUpdateElement,
    overridesById,
    profileKey,
    selectedElementId,
  ]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;
    refreshNativeElements();
    const observer = new MutationObserver(() => refreshNativeElements());
    observer.observe(root, {
      childList: true,
      characterData: true,
      subtree: true,
    });
    return () => observer.disconnect();
  }, [children, refreshNativeElements]);

  useEffect(() => () => {
    nativeLeafParentStylesRef.current.forEach((originalStyles, parent) => {
      restoreNativeLeafParentLayout(parent, originalStyles);
    });
    nativeLeafParentStylesRef.current.clear();
  }, []);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return undefined;

    function nativeTarget(event) {
      if (event.target.closest("[data-canvas-editor-ui]")) return null;
      const target = event.target.closest("[data-canvas-native-id]");
      return target && root.contains(target) ? target : null;
    }

    function handlePointerOver(event) {
      if (!canEdit) return;
      const target = nativeTarget(event);
      if (!target) return;
      const element = currentElementsRef.current.find(
        (item) => item.id === target.dataset.canvasNativeId,
      );
      if (element) {
        onSelectElement({
          id: element.id,
          label:
            overridesById.get(element.id)?.textOverride ||
            overridesById.get(element.id)?.labelOverride ||
            overridesById.get(element.id)?.label ||
            element.label,
          elementKind: element.elementKind,
          hasEditableTitle: element.hasEditableTitle,
          canEditText: element.canEditText,
          canEditPlaceholder: element.canEditPlaceholder,
          canEditAriaLabel: element.canEditAriaLabel,
          canEditAlt: element.canEditAlt,
        });
      }
    }

    function handleClick(event) {
      if (!canEdit) return;
      const target = nativeTarget(event);
      if (!target) return;
      const element = currentElementsRef.current.find(
        (item) => item.id === target.dataset.canvasNativeId,
      );
      if (element) {
        onSelectElement({
          id: element.id,
          label:
            overridesById.get(element.id)?.textOverride ||
            overridesById.get(element.id)?.labelOverride ||
            overridesById.get(element.id)?.label ||
            element.label,
          elementKind: element.elementKind,
          hasEditableTitle: element.hasEditableTitle,
          canEditText: element.canEditText,
          canEditPlaceholder: element.canEditPlaceholder,
          canEditAriaLabel: element.canEditAriaLabel,
          canEditAlt: element.canEditAlt,
        });
      }
    }

    function handleDragStart(event) {
      if (!canEdit) return;
      const target = nativeTarget(event);
      if (!target) return;
      const isLeaf = target.dataset.canvasNativeLeaf === "true";
      if (
        !isLeaf &&
        event.target.closest("button,input,select,textarea,a")
      ) {
        event.preventDefault();
        return;
      }
      dragSourceRef.current = target.dataset.canvasNativeId || "";
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        event.dataTransfer.setData(
          "application/x-fieldnotes-native-profile-element",
          dragSourceRef.current,
        );
      }
    }

    function handleDragOver(event) {
      if (canEdit && nativeTarget(event)) event.preventDefault();
    }

    function handleDrop(event) {
      if (!canEdit) return;
      const target = nativeTarget(event);
      if (!target) return;
      event.preventDefault();
      event.stopPropagation();
      const sourceId =
        event.dataTransfer?.getData(
          "application/x-fieldnotes-native-profile-element",
        ) || dragSourceRef.current;
      dragSourceRef.current = "";
      if (sourceId && sourceId !== target.dataset.canvasNativeId) {
        const source = currentElementsRef.current.find(
          (element) => element.id === sourceId,
        );
        const sourceIsLeaf =
          source?.element.dataset.canvasNativeLeaf === "true";
        const targetIsLeaf = target.dataset.canvasNativeLeaf === "true";
        if (
          !source ||
          sourceIsLeaf !== targetIsLeaf ||
          source.element.parentElement !== target.parentElement
        ) {
          return;
        }
        const orderedIds = orderedNativeSiblingEntries(
          target.parentElement,
          targetIsLeaf ? "leaf" : "container",
          currentElementsRef.current,
          overridesById,
        ).map(({ meta }) => meta.id);
        const sourceIndex = orderedIds.indexOf(sourceId);
        const targetIndex = orderedIds.indexOf(target.dataset.canvasNativeId);
        if (sourceIndex < 0 || targetIndex < 0) return;
        orderedIds.splice(sourceIndex, 1);
        orderedIds.splice(targetIndex, 0, sourceId);
        onReorderElements(
          orderedIds,
          targetIsLeaf ? "flexOrderWeight" : "gridOrderIndex",
        );
      }
    }

    function handlePointerDown(event) {
      if (!canEdit) return;
      const target = nativeTarget(event);
      if (
        target &&
        target.dataset.canvasNativeLeaf !== "true" &&
        event.target === target
      ) {
        resizeStartRef.current = {
          id: target.dataset.canvasNativeId,
          width: target.getBoundingClientRect().width,
          height: target.getBoundingClientRect().height,
        };
      } else {
        resizeStartRef.current = null;
      }
    }

    function handlePointerUp(event) {
      const startingSize = resizeStartRef.current;
      resizeStartRef.current = null;
      if (!canEdit || !startingSize) return;
      const target = nativeTarget(event);
      if (!target || target.dataset.canvasNativeId !== startingSize.id) return;
      const rect = target.getBoundingClientRect();
      if (
        Math.abs(rect.width - startingSize.width) > 2 ||
        Math.abs(rect.height - startingSize.height) > 2
      ) {
        onResizeElement(startingSize.id, rect.width, rect.height);
      }
    }

    root.addEventListener("pointerover", handlePointerOver);
    root.addEventListener("click", handleClick);
    root.addEventListener("dragstart", handleDragStart);
    root.addEventListener("dragover", handleDragOver);
    root.addEventListener("drop", handleDrop);
    root.addEventListener("pointerdown", handlePointerDown);
    root.addEventListener("pointerup", handlePointerUp);
    return () => {
      root.removeEventListener("pointerover", handlePointerOver);
      root.removeEventListener("click", handleClick);
      root.removeEventListener("dragstart", handleDragStart);
      root.removeEventListener("dragover", handleDragOver);
      root.removeEventListener("drop", handleDrop);
      root.removeEventListener("pointerdown", handlePointerDown);
      root.removeEventListener("pointerup", handlePointerUp);
    };
  }, [
    canEdit,
    onReorderElements,
    onResizeElement,
    onSelectElement,
    overridesById,
  ]);

  function updateStyle(field, value) {
    if (!selectedElementId) return;
    onUpdateElement(selectedElementId, {
      label: selectedDefaultLabel || "Profile element",
      styles: { [field]: value },
    });
  }

  function updateNumber(field, value) {
    if (!selectedElementId) return;
    const nextValue = ["gridOrderIndex", "flexOrderWeight"].includes(field)
      ? Math.round(value)
      : value;
    const patch = {
      label: selectedDefaultLabel || "Profile element",
      [field]: nextValue,
    };
    if (["gridOrderIndex", "flexOrderWeight"].includes(field)) {
      patch.order = nextValue;
    }
    onUpdateElement(selectedElementId, {
      ...patch,
    });
  }

  function selectedContainerNeighbor(direction) {
    if (selectedMeta?.elementKind !== "container") return null;
    return directionalNativeNeighbor(
      selectedMeta,
      direction,
      currentElementsRef.current,
      overridesById,
    );
  }

  function moveSelectedContainer(direction) {
    const neighbor = selectedContainerNeighbor(direction);
    if (!neighbor || !selectedMeta) return;
    const siblings = orderedNativeSiblingEntries(
      selectedMeta.element.parentElement,
      "container",
      currentElementsRef.current,
      overridesById,
    ).map(({ meta }) => meta.id);
    const currentIndex = siblings.indexOf(selectedMeta.id);
    const neighborIndex = siblings.indexOf(neighbor.id);
    if (currentIndex < 0 || neighborIndex < 0) return;
    [siblings[currentIndex], siblings[neighborIndex]] = [
      siblings[neighborIndex],
      siblings[currentIndex],
    ];
    onReorderElements(siblings, "gridOrderIndex");
  }

  function moveSelectedLeaf(direction) {
    if (!selectedMeta || selectedMeta.elementKind === "container") return;
    const siblings = orderedNativeSiblingEntries(
      selectedMeta.element.parentElement,
      "leaf",
      currentElementsRef.current,
      overridesById,
    ).map(({ meta }) => meta.id);
    const currentIndex = siblings.indexOf(selectedMeta.id);
    const neighborIndex = currentIndex + (direction === "left" ? -1 : 1);
    if (
      currentIndex < 0 ||
      neighborIndex < 0 ||
      neighborIndex >= siblings.length
    ) {
      return;
    }
    [siblings[currentIndex], siblings[neighborIndex]] = [
      siblings[neighborIndex],
      siblings[currentIndex],
    ];
    onReorderElements(siblings, "flexOrderWeight");
  }

  function canMoveSelectedLeaf(direction) {
    if (!selectedMeta || selectedMeta.elementKind === "container") return false;
    const siblings = orderedNativeSiblingEntries(
      selectedMeta.element.parentElement,
      "leaf",
      currentElementsRef.current,
      overridesById,
    );
    const currentIndex = siblings.findIndex(
      ({ meta }) => meta.id === selectedMeta.id,
    );
    const neighborIndex = currentIndex + (direction === "left" ? -1 : 1);
    return (
      currentIndex >= 0 &&
      neighborIndex >= 0 &&
      neighborIndex < siblings.length
    );
  }

  function updateColorDraft(field, value, setDraft) {
    setDraft(value);
    if (/^#?[\da-f]{6}$/i.test(String(value || "").trim())) {
      const normalized = normalizeHexColor(value);
      if (normalized) updateStyle(field, normalized);
    }
  }

  function commitColorDraft(field, value, setDraft, fallback) {
    const normalized = normalizeHexColor(value);
    if (!normalized) {
      setDraft(fallback);
      return;
    }
    setDraft(normalized);
    updateStyle(field, normalized);
  }

  function applyColorPreset(field, color, setDraft) {
    setDraft(color);
    updateStyle(field, color);
  }

  const availableToolboxItems = getProfileToolboxItems(profileKey);

  function addToolboxItem(item) {
    if (!canEdit || !onAddToolboxComponent) return;
    if (toolboxDragRef.current) {
      toolboxDragRef.current = false;
      return;
    }
    onAddToolboxComponent(item.id);
  }

  function startToolboxItemDrag(event, item) {
    if (!canEdit || !onAddToolboxComponent || !event.dataTransfer) {
      event.preventDefault();
      return;
    }
    toolboxDragRef.current = true;
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(PROFILE_TOOLBOX_DRAG_TYPE, item.id);
  }

  function endToolboxItemDrag() {
    window.setTimeout(() => {
      toolboxDragRef.current = false;
    }, 0);
  }

  function commitLabel() {
    const nextLabel = labelDraft.trim();
    if (!selectedElementId || !nextLabel) {
      setLabelDraft(selectedLabel);
      return;
    }
    onUpdateElement(selectedElementId, {
      label: selectedDefaultLabel || "Profile element",
      labelOverride:
        nextLabel === selectedDefaultLabel ? null : nextLabel,
    });
  }

  function commitStringOverride(field, draft, original, setDraft, maxLength) {
    if (!selectedElementId) return;
    const value = draft.slice(0, maxLength);
    if (!value.trim()) {
      setDraft(original || "");
      onUpdateElement(selectedElementId, {
        label: selectedDefaultLabel || "Profile element",
        [field]: null,
      });
      return;
    }
    onUpdateElement(selectedElementId, {
      label: selectedDefaultLabel || "Profile element",
      [field]: value === original ? null : value,
    });
  }

  return (
    <div
      className={className || "dynamic-mesh-native-content"}
      data-canvas-native-profile={profileKey}
      ref={rootRef}
    >
      {children}
      {canEdit && selectedElementId && (
        <aside
          aria-label="Selected profile element editor"
          className="native-canvas-editor"
          data-canvas-editor-ui="true"
          role="region"
        >
          <div className="native-canvas-editor__heading">
            <div>
              <span>{selectedKindLabel}</span>
              <strong>{selectedLabel || "Selected block"}</strong>
            </div>
            <button
              aria-label="Close profile element editor"
              onClick={() => onSelectElement(null)}
              type="button"
            >
              Close
            </button>
          </div>
          {selectedMeta?.elementKind === "container" && (
            <details
              className="native-canvas-toolbox"
              data-canvas-editor-ui="true"
              open
            >
              <summary>🧰 Component Toolbox Shelf</summary>
              <p>Drag a block into the canvas below, or select it to add it.</p>
              <div className="native-canvas-toolbox__scroll">
                 {Array.from(
                   new Set(availableToolboxItems.map((item) => item.category)),
                 ).map((category) => (
                  <section
                    aria-label={category}
                    className="native-canvas-toolbox__category"
                    key={category}
                  >
                    <h4>{category}</h4>
                    <div className="native-canvas-toolbox__grid">
                       {availableToolboxItems
                        .filter((item) => item.category === category)
                        .map((item) => (
                          <button
                            aria-label={`Add ${item.label} to canvas`}
                            className="native-canvas-toolbox__item"
                            data-canvas-editor-ui="true"
                            draggable={canEdit && Boolean(onAddToolboxComponent)}
                            key={item.id}
                            onClick={() => addToolboxItem(item)}
                            onDragEnd={endToolboxItemDrag}
                            onDragStart={(event) => startToolboxItemDrag(event, item)}
                            type="button"
                          >
                            {item.label}
                          </button>
                        ))}
                    </div>
                  </section>
                ))}
              </div>
            </details>
          )}
          {selectedMeta && selectedMeta.elementKind !== "container" && (
            <p className="native-canvas-editor__note">
              Display edits do not replace form values or existing profile actions.
            </p>
          )}
          {selectedMeta?.hasEditableTitle && (
            <label className="native-canvas-editor__field">
              Visible title
              <input
                aria-label="Native profile title"
                maxLength={120}
                onBlur={commitLabel}
                onChange={(event) => setLabelDraft(event.target.value)}
                value={labelDraft}
              />
            </label>
          )}
          {selectedMeta?.canEditText && (
            <label className="native-canvas-editor__field native-canvas-editor__field--wide">
              Visible text
              <textarea
                aria-label="Native visible text"
                maxLength={1_000}
                onBlur={() =>
                  commitStringOverride(
                    "textOverride",
                    textDraft,
                    selectedMeta.text,
                    setTextDraft,
                    1_000,
                  )
                }
                onChange={(event) => setTextDraft(event.target.value)}
                rows={3}
                value={textDraft}
              />
            </label>
          )}
          {selectedMeta?.canEditPlaceholder && (
            <label className="native-canvas-editor__field">
              Placeholder
              <input
                aria-label="Native placeholder"
                maxLength={240}
                onBlur={() =>
                  commitStringOverride(
                    "placeholderOverride",
                    placeholderDraft,
                    selectedMeta.placeholder,
                    setPlaceholderDraft,
                    240,
                  )
                }
                onChange={(event) => setPlaceholderDraft(event.target.value)}
                value={placeholderDraft}
              />
            </label>
          )}
          {selectedMeta?.canEditAriaLabel && (
            <label className="native-canvas-editor__field">
              Accessible label
              <input
                aria-label="Native accessible label"
                maxLength={120}
                onBlur={() =>
                  commitStringOverride(
                    "ariaLabelOverride",
                    ariaLabelDraft,
                    selectedMeta.ariaLabel,
                    setAriaLabelDraft,
                    120,
                  )
                }
                onChange={(event) => setAriaLabelDraft(event.target.value)}
                value={ariaLabelDraft}
              />
            </label>
          )}
          {selectedMeta?.canEditAlt && (
            <label className="native-canvas-editor__field">
              Image description
              <input
                aria-label="Native image description"
                maxLength={240}
                onBlur={() =>
                  commitStringOverride(
                    "altOverride",
                    altDraft,
                    selectedMeta.alt,
                    setAltDraft,
                    240,
                  )
                }
                onChange={(event) => setAltDraft(event.target.value)}
                value={altDraft}
              />
            </label>
          )}
          {selectedElement?.labelOverride && (
            <button
              className="native-canvas-editor__restore"
              onClick={() => {
                setLabelDraft(selectedDefaultLabel);
                onUpdateElement(selectedElementId, {
                  label: selectedDefaultLabel || "Profile element",
                  labelOverride: null,
                });
              }}
              type="button"
            >
              Restore original title
            </button>
          )}
          {selectedElement?.textOverride && (
            <button
              className="native-canvas-editor__restore"
              onClick={() => {
                setTextDraft(selectedMeta?.text || "");
                onUpdateElement(selectedElementId, {
                  label: selectedDefaultLabel || "Profile element",
                  textOverride: null,
                });
              }}
              type="button"
            >
              Restore original text
            </button>
          )}
          {selectedMeta?.elementKind === "container" &&
            !selectedMeta?.hasEditableTitle && (
            <p className="native-canvas-editor__note">
              This block has no plain-text title; its labels and data remain connected to the profile.
            </p>
          )}
          <div className="native-canvas-editor__grid">
            <NativeNumberField
              label="Font size"
              min={8}
              max={48}
              value={selectedStyles.fontSize ?? 14}
              onCommit={(value) => updateStyle("fontSize", value)}
            />
            <NativeNumberField
              label="Font weight"
              min={300}
              max={900}
              value={selectedStyles.fontWeight ?? 400}
              onCommit={(value) =>
                updateStyle("fontWeight", Math.round(value / 100) * 100)
              }
            />
            <label className="native-canvas-editor__field">
              Font family
              <select
                aria-label="Native font family"
                onChange={(event) => updateStyle("fontFamily", event.target.value)}
                value={selectedStyles.fontFamily || "system"}
              >
                <option value="system">System</option>
                <option value="serif">Serif</option>
                <option value="sans-serif">Sans serif</option>
                <option value="monospace">Monospace</option>
              </select>
            </label>
            <label className="native-canvas-editor__field">
              Text alignment
              <select
                aria-label="Native text alignment"
                onChange={(event) => updateStyle("textAlign", event.target.value)}
                value={selectedStyles.textAlign || "left"}
              >
                <option value="left">Left</option>
                <option value="center">Center</option>
                <option value="right">Right</option>
              </select>
            </label>
            <div className="native-canvas-editor__field">
              <span className="native-canvas-editor__field-label">Text color</span>
              <span className="native-canvas-editor__color-input">
                <span
                  aria-hidden="true"
                  className="native-canvas-editor__color-swatch"
                  style={{ backgroundColor: selectedTextColor }}
                />
                <input
                aria-label="Native text color"
                  autoComplete="off"
                  maxLength={7}
                  onBlur={() =>
                    commitColorDraft(
                      "color",
                      textColorDraft,
                      setTextColorDraft,
                      selectedTextColor,
                    )
                  }
                  onChange={(event) =>
                    updateColorDraft("color", event.target.value, setTextColorDraft)
                  }
                  placeholder="#302B27"
                  spellCheck={false}
                  type="text"
                  value={textColorDraft}
                />
              </span>
              <NativeSpectrumPicker
                label="Text color"
                onChange={(value) =>
                  updateColorDraft("color", value, setTextColorDraft)
                }
                value={selectedTextColor}
              />
              <span className="native-canvas-spectrum__presets">
                <button
                  onClick={() =>
                    applyColorPreset("color", "#8F5C38", setTextColorDraft)
                  }
                  type="button"
                >
                  Walnut (#8F5C38)
                </button>
                <button
                  onClick={() =>
                    applyColorPreset("color", "#0A0A0A", setTextColorDraft)
                  }
                  type="button"
                >
                  Obsidian (#0A0A0A)
                </button>
              </span>
            </div>
            <div className="native-canvas-editor__field">
              <span className="native-canvas-editor__field-label">Background</span>
              <span className="native-canvas-editor__color-input">
                <span
                  aria-hidden="true"
                  className="native-canvas-editor__color-swatch"
                  style={{ backgroundColor: selectedBackgroundColor }}
                />
                <input
                aria-label="Native background color"
                  autoComplete="off"
                  maxLength={7}
                  onBlur={() =>
                    commitColorDraft(
                      "backgroundColor",
                      backgroundColorDraft,
                      setBackgroundColorDraft,
                      selectedBackgroundColor,
                    )
                  }
                  onChange={(event) =>
                    updateColorDraft(
                      "backgroundColor",
                      event.target.value,
                      setBackgroundColorDraft,
                    )
                  }
                  placeholder="#FFFFFF"
                  spellCheck={false}
                  type="text"
                  value={backgroundColorDraft}
                />
              </span>
              <NativeSpectrumPicker
                label="Background"
                onChange={(value) =>
                  updateColorDraft("backgroundColor", value, setBackgroundColorDraft)
                }
                value={selectedBackgroundColor}
              />
              <span className="native-canvas-spectrum__presets">
                <button
                  onClick={() =>
                    applyColorPreset("backgroundColor", "#8F5C38", setBackgroundColorDraft)
                  }
                  type="button"
                >
                  Walnut (#8F5C38)
                </button>
                <button
                  onClick={() =>
                    applyColorPreset("backgroundColor", "#0A0A0A", setBackgroundColorDraft)
                  }
                  type="button"
                >
                  Obsidian (#0A0A0A)
                </button>
              </span>
            </div>
            <NativeNumberField
              label="Padding"
              min={0}
              max={48}
              value={selectedStyles.padding ?? 16}
              onCommit={(value) => updateStyle("padding", value)}
            />
            <NativeNumberField
              label="Margin"
              min={0}
              max={32}
              value={selectedStyles.margin ?? 0}
              onCommit={(value) => updateStyle("margin", value)}
            />
            <NativeNumberField
              label="Width (px)"
              min={120}
              max={1_440}
              value={selectedStyles.widthPx ?? 360}
              onCommit={(value) => updateStyle("widthPx", value)}
            />
            <NativeNumberField
              label="Height (px)"
              min={80}
              max={1_600}
              value={selectedStyles.heightPx ?? 180}
              onCommit={(value) => updateStyle("heightPx", value)}
            />
            {selectedMeta?.elementKind === "container" && (
              <>
                <NativeNumberField
                  label="Grid order index"
                  min={-1_000}
                  max={1_000}
                  value={
                    selectedElement?.gridOrderIndex ??
                    selectedElement?.order ??
                    selectedMeta?.order ??
                    0
                  }
                  onCommit={(value) => updateNumber("gridOrderIndex", value)}
                />
                <NativeNumberField
                  label="Column span"
                  min={1}
                  max={12}
                  value={selectedElement?.gridColumnSpan ?? 1}
                  onCommit={(value) => updateNumber("gridColumnSpan", value)}
                />
                <NativeNumberField
                  label="Row span"
                  min={1}
                  max={30}
                  value={selectedElement?.gridRowSpan ?? 1}
                  onCommit={(value) => updateNumber("gridRowSpan", value)}
                />
              </>
            )}
          </div>
          <section
            aria-label="Component Arrangement & Mesh Positioning"
            className="native-canvas-arrangement"
          >
            <h3>🎯 Component Arrangement &amp; Mesh Positioning</h3>
            {selectedMeta?.elementKind === "container" ? (
              <>
                <p>Move this outer container to a neighboring grid position.</p>
                <div
                  aria-label="Outer container grid position"
                  className="native-canvas-arrangement__directional"
                  role="group"
                >
                  {[
                    ["up", "⬆️ Move Up"],
                    ["left", "⬅️ Shift Left"],
                    ["right", "➡️ Shift Right"],
                    ["down", "⬇️ Move Down"],
                  ].map(([direction, label]) => (
                    <button
                      aria-label={label}
                      className="native-canvas-arrangement__button"
                      disabled={!selectedContainerNeighbor(direction)}
                      key={direction}
                      onClick={() => moveSelectedContainer(direction)}
                      type="button"
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </>
            ) : (
              <>
                <p>Adjust this leaf element’s rendering order inside its container.</p>
                <div className="native-canvas-arrangement__leaf-controls">
                  <NativeNumberField
                    label="Element Weight"
                    min={-1_000}
                    max={1_000}
                    value={
                      selectedElement?.flexOrderWeight ??
                      selectedElement?.order ??
                      selectedMeta?.order ??
                      0
                    }
                    onCommit={(value) =>
                      updateNumber("flexOrderWeight", value)
                    }
                  />
                  <button
                    aria-label="⬅️ Move Left"
                    className="native-canvas-arrangement__button"
                    disabled={!canMoveSelectedLeaf("left")}
                    onClick={() => moveSelectedLeaf("left")}
                    type="button"
                  >
                    ⬅️ Move Left
                  </button>
                  <button
                    aria-label="➡️ Move Right"
                    className="native-canvas-arrangement__button"
                    disabled={!canMoveSelectedLeaf("right")}
                    onClick={() => moveSelectedLeaf("right")}
                    type="button"
                  >
                    ➡️ Move Right
                  </button>
                </div>
              </>
            )}
          </section>
        </aside>
      )}
    </div>
  );
}