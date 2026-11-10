import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { confirmCanvasComponentDeletion } from "../utils/canvasComponentDeletion";
import { useCanvasLayoutStream } from "./useCanvasLayoutStream";
import DynamicEditableMeshWrapper, {
  NativeSpectrumPicker,
  PROFILE_COMPONENT_TOOLBOX_ITEMS,
  PROFILE_TOOLBOX_DRAG_TYPE,
  normalizeHexColor,
} from "./DynamicEditableMeshWrapper";
import {
  CANVAS_STUDIO_SLOT_PREFIX,
  CANVAS_STUDIO_VIEWPORT,
} from "./canvasStudioConfig";
import {
  STUDIO_FONT_KEYS,
  STUDIO_THEMES,
  applyStudioThemeToComponentTree,
  getStudioTheme,
  getThemedComponentStyles,
} from "../utils/studioThemes";
import {
  MASTER_OVERVIEW_CONTAINER_TYPE,
  canInsertCanvasComponentTree,
  canvasTreeStats,
  filterCanvasComponentTreeForRole,
  findCanvasComponentLocation,
  flattenCanvasComponentTree,
  insertCanvasComponentTree,
  moveCanvasComponentTree,
  removeCanvasComponentTree,
  updateCanvasComponentTree,
} from "./canvasComponentTree";
import "./DynamicMeshContainer.css";
import "./NavigationLinkMenuBar.css";

const VIEWPORT_BY_PAGE_TARGET = {
  MASTER_CONTROL: "Master Control Room Dashboard",
  PRINCIPAL_COCKPIT: "Principal Overview Dashboard",
  HOD_ANALYTICS: "Grade HOD Analytics Console",
  TEACHER_DASHBOARD: "Educator Workspace View",
  PARENT_PORTAL: "Parent Insights Dashboard",
  SCHOLAR_PLAYGROUND: "Scholar Simulation Portal",
};

const VIEWPORT_BY_ROLE = {
  MASTER_ADMIN: VIEWPORT_BY_PAGE_TARGET.MASTER_CONTROL,
  PRINCIPAL: VIEWPORT_BY_PAGE_TARGET.PRINCIPAL_COCKPIT,
  GRADE_HOD: VIEWPORT_BY_PAGE_TARGET.HOD_ANALYTICS,
  EDUCATOR: VIEWPORT_BY_PAGE_TARGET.TEACHER_DASHBOARD,
  PARENT: VIEWPORT_BY_PAGE_TARGET.PARENT_PORTAL,
  SCHOLAR: VIEWPORT_BY_PAGE_TARGET.SCHOLAR_PLAYGROUND,
};

const PAGE_TARGET_BY_ROLE = {
  MASTER_ADMIN: "MASTER_CONTROL",
  PRINCIPAL: "PRINCIPAL_COCKPIT",
  GRADE_HOD: "HOD_ANALYTICS",
  EDUCATOR: "TEACHER_DASHBOARD",
  PARENT: "PARENT_PORTAL",
  SCHOLAR: "SCHOLAR_PLAYGROUND",
};

const ROLE_KEY_BY_PAGE_TARGET = {
  MASTER_CONTROL: "master_admin",
  PRINCIPAL_COCKPIT: "principal",
  HOD_ANALYTICS: "grade_hod",
  TEACHER_DASHBOARD: "educator",
  PARENT_PORTAL: "parent",
  SCHOLAR_PLAYGROUND: "scholar",
};

const FONT_FAMILY_BY_KEY = {
  system: "system-ui, sans-serif",
  serif: "Georgia, serif",
  "sans-serif": "Arial, sans-serif",
  monospace: "ui-monospace, monospace",
  ...Object.fromEntries(
    STUDIO_THEMES.map((theme) => [theme.fontKey, theme.fontStack]),
  ),
};

const CANVAS_MEDIA_VAULT_DRAG_TYPE = "application/x-fieldnotes-media-vault";
const CANVAS_MEDIA_VAULT_MAX_ITEMS = 16;
const CANVAS_MEDIA_VAULT_MAX_RASTER_BYTES = 180_000;
const CANVAS_MEDIA_VAULT_MAX_SVG_BYTES = 12_000;
const CANVAS_MEDIA_VAULT_MAX_DATA_URL_CHARS = 245_000;

function safeCanvasAssetUrl(value) {
  if (typeof value !== "string" || value !== value.trim() || /[\\\u0000-\u001f]/.test(value)) {
    return false;
  }
  if (value.startsWith("/") && !value.startsWith("//")) return true;
  try {
    const parsed = new URL(value);
    return parsed.protocol === "https:" && !parsed.username && !parsed.password;
  } catch {
    return false;
  }
}

function acceptsCanvasImageAsset(component) {
  const type = String(component?.type || "").toLowerCase();
  return (
    type === "image_asset" ||
    type.includes("graphic") ||
    Boolean(component?.imageSettings)
  );
}

const LEGACY_ACTION_BINDING = {
  refresh_data_stream: "REFRESH_LAYOUT",
  acknowledge_telemetry_alert: "ACKNOWLEDGE_TELEMETRY_ALERT",
  unlock_simulation_canvas: "UNLOCK_SIMULATION_CANVAS",
  trigger_otp_gate: "TRIGGER_OTP_GATE",
  rollover_past_pacing: "ROLLOVER_PAST_PACING",
};

function clamp(value, min, max, fallback) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.min(max, Math.max(min, parsed)) : fallback;
}

function CanvasNumberField({ label, value, min, max, onCommit }) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    setDraft(String(value));
  }, [value]);

  function commit() {
    if (draft === "") {
      setDraft(String(value));
      return;
    }
    const nextValue = clamp(draft, min, max, value);
    setDraft(String(nextValue));
    onCommit(nextValue);
  }

  return (
    <label className="dynamic-mesh-block__editor-field">
      {label}
      <input
        aria-label={label}
        max={max}
        min={min}
        onBlur={commit}
        onChange={(event) => setDraft(event.target.value)}
        type="number"
        value={draft}
      />
    </label>
  );
}

function safeImageDataUrl(value) {
  return typeof value === "string" &&
    /^data:image\/(?:png|jpeg|webp);base64,[a-zA-Z0-9+/=]+$/.test(value);
}

function safeInternalOrHttpsUrl(value) {
  if (
    typeof value !== "string" ||
    value.length > 2_048 ||
    value !== value.trim() ||
    value.includes("\\") ||
    /[\u0000-\u001f]/.test(value)
  ) {
    return false;
  }
  try {
    const parsed = new URL(value, window.location.origin);
    if (value.startsWith("/") && !value.startsWith("//")) {
      return parsed.origin === window.location.origin;
    }
    return (
      parsed.protocol === "https:" &&
      Boolean(parsed.hostname) &&
      !parsed.username &&
      !parsed.password
    );
  } catch {
    return false;
  }
}

function getYouTubeEmbedUrl(url) {
  if (!url || typeof url !== "string") return "";
  const trimmedUrl = url.trim();
  if (!trimmedUrl) return "";
  const regExp =
    /^.*(youtu\.be\/|v\/|u\/\w\/|embed\/|watch\?v=|&v=|shorts\/|live\/)([^#&?]*).*/i;
  const match = trimmedUrl.match(regExp);
  const videoId = match?.[2] || "";
  return /^[A-Za-z0-9_-]{11}$/.test(videoId)
    ? `https://www.youtube-nocookie.com/embed/${videoId}`
    : trimmedUrl;
}

function resolveCanvasVideoSource(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const videoUrl = value.trim();
  const isRawShortcut =
    /^(?:watch\?v=|&v=|v\/|u\/\w\/|embed\/|shorts\/|live\/)/i.test(videoUrl);
  const isBareYouTubeDomain =
    /^(?:(?:www|m)\.)?(?:youtube\.com|youtu\.be)\//i.test(videoUrl);
  const parseableUrl = isBareYouTubeDomain
    ? `https://${videoUrl}`
    : videoUrl;
  if (!isRawShortcut && !safeInternalOrHttpsUrl(parseableUrl)) return null;
  try {
    const parsed = new URL(parseableUrl, window.location.origin);
    if (parsed.pathname.toLowerCase().endsWith(".mp4")) {
      return { kind: "mp4", src: videoUrl };
    }
    const host = parsed.hostname.toLowerCase();
    const isYouTubeHost = [
      "youtube.com",
      "www.youtube.com",
      "m.youtube.com",
      "youtu.be",
      "www.youtu.be",
    ].includes(host);
    if (!isYouTubeHost && !isRawShortcut) {
      return null;
    }
    const embedUrl = getYouTubeEmbedUrl(videoUrl);
    if (!/^https:\/\/www\.youtube-nocookie\.com\/embed\/[A-Za-z0-9_-]{11}$/.test(embedUrl)) {
      return null;
    }
    return {
      kind: "youtube",
      src: embedUrl,
    };
  } catch {
    return null;
  }
}

function safeSvgImageSource(markup) {
  if (
    typeof markup !== "string" ||
    !markup ||
    markup.length > 12_000 ||
    /<!doctype|<!entity|javascript:|url\s*\(/i.test(markup) ||
    typeof DOMParser === "undefined"
  ) {
    return "";
  }
  try {
    const document = new DOMParser().parseFromString(markup, "image/svg+xml");
    const root = document.documentElement;
    const allowedTags = new Set([
      "svg", "g", "path", "circle", "ellipse", "rect", "line",
      "polyline", "polygon", "title", "desc",
    ]);
    const allowedAttributes = new Set([
      "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2",
      "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke",
      "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
      "stroke-dashoffset", "fill-rule", "opacity", "fill-opacity",
      "stroke-opacity", "id", "role", "aria-label",
    ]);
    if (
      root.localName !== "svg" ||
      (root.namespaceURI && root.namespaceURI !== "http://www.w3.org/2000/svg") ||
      document.querySelector("parsererror")
    ) {
      return "";
    }
    const nodes = [{ element: root, depth: 0 }];
    let count = 0;
    while (nodes.length) {
      const { element: node, depth } = nodes.pop();
      count += 1;
      if (count > 160 || depth > 12 || !allowedTags.has(node.localName)) return "";
      for (const attribute of Array.from(node.attributes)) {
        if (
          attribute.name === "xmlns" &&
          node === root &&
          attribute.value === "http://www.w3.org/2000/svg"
        ) {
          continue;
        }
        if (
          !allowedAttributes.has(attribute.name) ||
          attribute.value.length > 8_000 ||
          /javascript:|data:|url\s*\(|https?:|\/\//i.test(attribute.value)
        ) {
          return "";
        }
      }
      const directText = Array.from(node.childNodes)
        .filter((child) => child.nodeType === 3)
        .map((child) => child.textContent || "")
        .join("");
      if (!["title", "desc"].includes(node.localName) && directText.trim()) {
        return "";
      }
      if (["title", "desc"].includes(node.localName) && directText.length > 240) {
        return "";
      }
      nodes.push(
        ...Array.from(node.children).map((child) => ({
          element: child,
          depth: depth + 1,
        })),
      );
    }
    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(markup)}`;
  } catch {
    return "";
  }
}

function cleanCanvasMediaAssetName(value) {
  return String(value || "Brand graphic")
    .replace(/[\u0000-\u001f\u007f]/g, "")
    .trim()
    .slice(0, 120) || "Brand graphic";
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 32_768));
  }
  return btoa(binary);
}

function rasterizeSanitizedSvg(markup) {
  const source = safeSvgImageSource(markup);
  if (!source || typeof Image === "undefined" || typeof DOMParser === "undefined") {
    return Promise.reject(new Error("This SVG cannot be safely converted to an image."));
  }

  const root = new DOMParser().parseFromString(markup, "image/svg+xml").documentElement;
  const viewBox = (root.getAttribute("viewBox") || "")
    .trim()
    .split(/[\s,]+/)
    .map(Number);
  const viewBoxWidth =
    viewBox.length === 4 && Number.isFinite(viewBox[2]) && viewBox[2] > 0
      ? viewBox[2]
      : 0;
  const viewBoxHeight =
    viewBox.length === 4 && Number.isFinite(viewBox[3]) && viewBox[3] > 0
      ? viewBox[3]
      : 0;
  const declaredWidth = Number(root.getAttribute("width"));
  const declaredHeight = Number(root.getAttribute("height"));
  const sourceWidth =
    Number.isFinite(declaredWidth) && declaredWidth > 0
      ? declaredWidth
      : viewBoxWidth || 512;
  const sourceHeight =
    Number.isFinite(declaredHeight) && declaredHeight > 0
      ? declaredHeight
      : viewBoxHeight || sourceWidth;
  const scale = Math.min(1, 768 / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));

  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = width;
        canvas.height = height;
        const context = canvas.getContext("2d");
        if (!context) {
          reject(new Error("This browser cannot convert SVG graphics."));
          return;
        }
        context.drawImage(image, 0, 0, width, height);
        const dataUrl = canvas.toDataURL("image/png");
        if (
          !safeImageDataUrl(dataUrl) ||
          dataUrl.length > CANVAS_MEDIA_VAULT_MAX_DATA_URL_CHARS
        ) {
          reject(new Error("The converted SVG is too large to place on the canvas."));
          return;
        }
        resolve(dataUrl);
      } catch {
        reject(new Error("The SVG could not be converted to a canvas image."));
      }
    };
    image.onerror = () =>
      reject(new Error("The SVG preview could not be rendered safely."));
    image.src = source;
  });
}

async function readCanvasMediaVaultFile(file, id) {
  const name = cleanCanvasMediaAssetName(file?.name);
  const fileName = String(file?.name || "").toLowerCase();
  const declaredType = String(file?.type || "").toLowerCase();

  if (declaredType === "image/svg+xml" || fileName.endsWith(".svg")) {
    if (file.size > CANVAS_MEDIA_VAULT_MAX_SVG_BYTES) {
      throw new Error(`${name}: SVG files must be 12 KB or smaller.`);
    }
    const svgMarkup = await file.text();
    const previewUrl = safeSvgImageSource(svgMarkup);
    if (!previewUrl) {
      throw new Error(`${name}: this SVG contains unsupported or unsafe markup.`);
    }
    const dataUrl = await rasterizeSanitizedSvg(svgMarkup);
    return { id, name, dataUrl, svgMarkup, previewUrl };
  }

  if (file.size > CANVAS_MEDIA_VAULT_MAX_RASTER_BYTES) {
    throw new Error(`${name}: PNG and JPG files must be 180 KB or smaller.`);
  }
  const buffer = await file.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  const isPng =
    bytes.length >= 8 &&
    bytes[0] === 0x89 &&
    bytes[1] === 0x50 &&
    bytes[2] === 0x4e &&
    bytes[3] === 0x47 &&
    bytes[4] === 0x0d &&
    bytes[5] === 0x0a &&
    bytes[6] === 0x1a &&
    bytes[7] === 0x0a;
  const isJpeg =
    bytes.length >= 3 &&
    bytes[0] === 0xff &&
    bytes[1] === 0xd8 &&
    bytes[2] === 0xff;
  const mimeType = isPng ? "image/png" : isJpeg ? "image/jpeg" : "";
  if (!mimeType) {
    throw new Error(`${name}: choose a valid PNG, JPG, or SVG file.`);
  }
  const dataUrl = `data:${mimeType};base64,${arrayBufferToBase64(buffer)}`;
  if (
    !safeImageDataUrl(dataUrl) ||
    dataUrl.length > CANVAS_MEDIA_VAULT_MAX_DATA_URL_CHARS
  ) {
    throw new Error(`${name}: this image is too large to place on the canvas.`);
  }
  return { id, name, dataUrl, svgMarkup: "", previewUrl: dataUrl };
}

function readCanvasMediaVaultPayload(dataTransfer) {
  const payload = dataTransfer?.getData(CANVAS_MEDIA_VAULT_DRAG_TYPE) || "";
  if (!payload || payload.length > CANVAS_MEDIA_VAULT_MAX_DATA_URL_CHARS + 16_000) {
    return null;
  }
  try {
    const parsed = JSON.parse(payload);
    if (parsed?.version !== 1) return null;
    const dataUrl =
      typeof parsed?.dataUrl === "string" &&
      parsed.dataUrl.length <= CANVAS_MEDIA_VAULT_MAX_DATA_URL_CHARS &&
      safeImageDataUrl(parsed.dataUrl)
        ? parsed.dataUrl
        : "";
    const svgMarkup =
      typeof parsed?.svgMarkup === "string" &&
      parsed.svgMarkup.length <= CANVAS_MEDIA_VAULT_MAX_SVG_BYTES &&
      safeSvgImageSource(parsed.svgMarkup)
        ? parsed.svgMarkup
        : "";
    if (!dataUrl && !svgMarkup) return null;
    return {
      name: cleanCanvasMediaAssetName(parsed?.name),
      dataUrl,
      svgMarkup,
    };
  } catch {
    return null;
  }
}

function acceptsCanvasMediaVaultDrop(component) {
  return (
    acceptsCanvasImageAsset(component) ||
    ["master_overview_container", "rich_text_block", "text_observation_block"].includes(
      String(component?.type || "").toLowerCase(),
    )
  );
}

function resolveVariableBinding(binding, context) {
  const values = {
    CURRENT_PROFILE_NAME: context.user?.display_name || context.user?.name || "",
    CURRENT_PROFILE_ROLE: context.user?.role || context.roleName || "",
    ACTIVE_TENANT_ID: context.blueprint?.tenant_id
      ? String(context.blueprint.tenant_id)
      : "",
    LAYOUT_UPDATED_AT: context.blueprint?.last_updated_at || "",
  };
  return values[binding] || "";
}

function blockVisualStyle(component, layoutMode, gridColumns) {
  const styles = component.styles || {};
  const visualStyle = {
    padding: clamp(styles.padding, 0, 48, 16),
    margin: clamp(styles.margin, 0, 32, 0),
    color: /^#[0-9a-fA-F]{6}$/.test(styles.color || "")
      ? styles.color
      : "#302B27",
    backgroundColor: /^#[0-9a-fA-F]{6}$/.test(styles.backgroundColor || "")
      ? styles.backgroundColor
      : "#FFFFFF",
    borderColor: /^#[0-9a-fA-F]{6}$/.test(styles.borderColor || "")
      ? styles.borderColor
      : "#D8D0C5",
    borderRadius: clamp(styles.borderRadius, 0, 32, 12),
    fontSize: clamp(styles.fontSize, 8, 48, 14),
    fontWeight: clamp(styles.fontWeight, 300, 900, 400),
    fontFamily: FONT_FAMILY_BY_KEY[styles.fontFamily] || FONT_FAMILY_BY_KEY.system,
    textAlign: ["left", "center", "right"].includes(styles.textAlign)
      ? styles.textAlign
      : "left",
    width: Number.isFinite(styles.widthPx)
      ? `${clamp(styles.widthPx, 120, 1_440, 360)}px`
      : undefined,
    height: Number.isFinite(styles.heightPx)
      ? `${clamp(styles.heightPx, 80, 1_600, 180)}px`
      : undefined,
    maxWidth: "100%",
    order: Number.isFinite(component.gridOrderIndex)
      ? component.gridOrderIndex
      : 0,
    transition: "all 0.2s ease",
  };

  if (layoutMode === "absolute") {
    return {
      ...visualStyle,
      position: "absolute",
      left: `${clamp(component.x, 0, 100, 0)}%`,
      top: `${clamp(component.y, 0, 100, 0)}%`,
      width: `${clamp(component.width, 1, 100, 50)}%`,
      height: `${clamp(component.height, 1, 100, 24)}%`,
      overflow: "auto",
    };
  }

  const columnStart = clamp(component.gridColumn, 1, gridColumns, 1);
  const columnSpan = clamp(component.gridColumnSpan, 1, gridColumns, 6);
  const rowStart = clamp(component.gridRow, 1, 30, 1);
  const rowSpan = clamp(component.gridRowSpan, 1, 30, 1);
  return {
    ...visualStyle,
    gridColumn: component.gridColumn
      ? `${columnStart} / span ${columnSpan}`
      : `span ${columnSpan}`,
    gridRow: component.gridRow ? `${rowStart} / span ${rowSpan}` : `span ${rowSpan}`,
  };
}

function DynamicMeshBlock({
  component,
  index,
  layoutMode,
  gridColumns,
  parentId = null,
  context,
  roleKey,
  onRefresh,
  canEdit,
  isEditing,
  isSelected,
  selectedComponentId,
  onSelect,
  onUpdate,
  onUpdateStyles,
  onRemove,
  onMergePrevious,
  onUngroup,
  onDragStart,
  onDragEnd,
  onDrop,
  onResize,
  onUploadImage,
  addToolboxComponent,
  subTabs = [],
  activeSubTabId = "",
  onNavigateTab,
}) {
  const [localValue, setLocalValue] = useState("");
  const [actionMessage, setActionMessage] = useState("");
  const [libraryExecutionState, setLibraryExecutionState] = useState("idle");
  const [libraryExecutionError, setLibraryExecutionError] = useState("");
  const [libraryAssets, setLibraryAssets] = useState([]);
  const actionBinding =
    component.functionBinding ||
    LEGACY_ACTION_BINDING[component.actionTrigger] ||
    "";
  const variableValue = resolveVariableBinding(
    component.variableBinding,
    context,
  );
  const hasExecutableLibraryBindings =
    component.nodeDefinition === "ELibraryDataNode" &&
    component.tenantFilterBinding === "ACTIVE_TENANT_ID" &&
    component.metadataStreamBinding === "ELIBRARY_ASSET_METADATA_V1";
  const styles = component.styles || {};
  const isStudioDropSlot =
    typeof component.groupId === "string" &&
    component.groupId.startsWith(CANVAS_STUDIO_SLOT_PREFIX);
  const imageData = component.imageSettings?.dataUrl;
  const imageSource = safeImageDataUrl(imageData)
    ? imageData
    : safeCanvasAssetUrl(component.imageSettings?.sourceUrl)
      ? component.imageSettings.sourceUrl
      : "";
  const videoUrl = component.href;
  const videoSource =
    component.type === "video_embed_block"
      ? resolveCanvasVideoSource(videoUrl)
      : null;
  const svgImageSource =
    component.type === "svg_graphic_block"
      ? safeSvgImageSource(component.svgMarkup)
      : "";
  const headingLevel =
    component.type === "rich_text_block" &&
    [1, 2, 3].includes(Number(component.headingLevel))
      ? Number(component.headingLevel)
      : null;
  const isButton =
    component.type === "trigger_action_button" ||
    component.type === "custom_button_element";
  const isMasterOverviewContainer =
    component.type === MASTER_OVERVIEW_CONTAINER_TYPE;
  const childLayoutMode =
    component.childLayoutMode === "absolute" ? "absolute" : "grid";
  const childGridColumns = clamp(component.childGridColumns, 1, 12, 3);
  const childGap = clamp(component.childGap, 0, 40, 12);
  const childCanvasHeight = clamp(component.childCanvasHeight, 240, 1_600, 360);
  const childComponents = orderCanvasComponents(component.children || []);

  function runBoundAction(event) {
    event.stopPropagation();
    if (isEditing) {
      setActionMessage("Switch to preview mode to run this button.");
      return;
    }
    if (actionBinding === "REFRESH_LAYOUT") {
      void onRefresh();
      setActionMessage("Layout refreshed.");
      return;
    }
    if (["TRIGGER_OTP_GATE", "ROLLOVER_PAST_PACING"].includes(actionBinding)) {
      setActionMessage("Requesting secure server action…");
      fetch("/api/low-code/actions/execute", {
        method: "POST",
        credentials: "include",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify({ actionBinding }),
      })
        .then(async (response) => {
          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            const error = new Error(
              payload.detail || "The secure classroom action could not be completed.",
            );
            error.status = response.status;
            throw error;
          }
          return payload;
        })
        .then((payload) => {
          if (actionBinding === "TRIGGER_OTP_GATE") {
            if (!payload.code) throw new Error("The classroom gate did not return a code.");
            setActionMessage(
              `Classroom gate ${payload.targetLabel ? `for ${payload.targetLabel} ` : ""}code: ${payload.code}`,
            );
          } else {
            setActionMessage(
              payload.chapterTitle
                ? `Pacing rollover recorded for ${payload.chapterTitle}.`
                : "Pacing rollover recorded.",
            );
          }
        })
        .catch((error) => {
          setActionMessage(
            error.status === 401
              ? "Your session has expired. Sign in again to run this action."
              : error.status === 403
                ? "Your signed-in role cannot run this action."
                : error.status === 409
                  ? "The active class or planner context is unavailable."
                  : error.message || "The secure classroom action failed.",
          );
        });
      return;
    }
    if (
      actionBinding === "EXPORT_ROSTER_DATA" &&
      roleKey === "principal" &&
      context.user?.role === "PRINCIPAL"
    ) {
      window.dispatchEvent(
        new CustomEvent("fieldnotes:canvas-command", {
          detail: {
            command: "EXPORT_ROSTER_DATA",
            scope: component.exportScope || "scholar",
          },
        }),
      );
      setActionMessage("Roster export requested in this principal workspace.");
      return;
    }
    if (
      actionBinding === "LAUNCH_EXTERNAL_HYPERLINK" &&
      safeInternalOrHttpsUrl(component.href)
    ) {
      if (component.href.startsWith("/")) {
        window.location.assign(component.href);
      } else {
        window.open(component.href, "_blank", "noopener,noreferrer");
      }
      return;
    }
    if (actionBinding === "ACKNOWLEDGE_TELEMETRY_ALERT") {
      setActionMessage("No authenticated acknowledgement action is configured.");
      return;
    }
    if (actionBinding === "UNLOCK_SIMULATION_CANVAS") {
      setActionMessage("This action is not available from a layout block.");
    }
  }

  async function executeLibraryBinding() {
    if (!hasExecutableLibraryBindings || context.user?.role !== "SCHOLAR") return;
    setLibraryExecutionState("running");
    setLibraryExecutionError("");
    setLibraryAssets([]);
    try {
      const response = await fetch("/api/library/canvas-binding/execute", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ node_id: component.id }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "Approved E-Library metadata could not be loaded.",
        );
      }
      if (payload.execution_status !== "completed" || !Array.isArray(payload.assets)) {
        throw new Error("The E-Library returned an invalid execution result.");
      }
      setLibraryAssets(payload.assets);
      setLibraryExecutionState("completed");
    } catch (error) {
      setLibraryExecutionState("error");
      setLibraryExecutionError(
        error instanceof Error
          ? error.message
          : "Approved E-Library metadata could not be loaded.",
      );
    }
  }

  function commitNativeResize(event) {
    if (!isEditing || event.target !== event.currentTarget) return;
    const blockRect = event.currentTarget.getBoundingClientRect();
    const canvasRect =
      event.currentTarget
        .closest("[data-canvas-nested-layout]")
        ?.getBoundingClientRect() ||
      event.currentTarget
        .closest("[data-canvas-root-layout]")
        ?.getBoundingClientRect();
    if (!canvasRect) return;
    if (layoutMode === "absolute") {
      onResize(component.id, {
        width: Math.round((blockRect.width / canvasRect.width) * 100),
        height: Math.round((blockRect.height / canvasRect.height) * 100),
      }, parentId, layoutMode);
      return;
    }
    onResize(component.id, {
      widthPx: Math.round(blockRect.width),
      heightPx: Math.round(blockRect.height),
    }, parentId, layoutMode);
  }

  return (
    <article
      aria-label={component.label || `${component.type} canvas component`}
      className={[
        "dynamic-mesh-block",
        "w-full",
        "box-border",
        isMasterOverviewContainer
          ? "is-master-overview-container"
          : "",
        isStudioDropSlot ? "dynamic-mesh-block--studio-slot" : "",
        isEditing ? "is-canvas-editable" : "",
        isSelected ? "is-canvas-selected" : "",
      ].filter(Boolean).join(" ")}
      data-canvas-drop-slot={isStudioDropSlot ? "true" : undefined}
      data-canvas-component-type={component.type}
      data-layout-group={component.groupId || undefined}
      key={component.id || `${component.type}-${index}`}
      draggable={Boolean(isEditing)}
      onClick={(event) => {
        event.stopPropagation();
        if (isEditing) onSelect(component.id);
      }}
      onDragEnd={onDragEnd}
      onDragOver={(event) => {
        if (isEditing) event.preventDefault();
      }}
      onDragStart={(event) => {
        if (!isEditing) {
          event.preventDefault();
          return;
        }
        if (event.target.closest(".dynamic-mesh-block__editor")) {
          event.preventDefault();
          return;
        }
        onDragStart(event, component.id);
        event.stopPropagation();
      }}
      onKeyDown={(event) => {
        if (
          isEditing &&
          event.target === event.currentTarget &&
          (event.key === "Enter" || event.key === " ")
        ) {
          event.preventDefault();
          onSelect(component.id);
        }
      }}
      onMouseEnter={() => {
        if (isEditing) onSelect(component.id);
      }}
      onDragOver={(event) => {
        const transferTypes = Array.from(event.dataTransfer?.types || []);
        if (
          isEditing &&
          (
            (acceptsCanvasImageAsset(component) &&
              transferTypes.some((type) =>
                ["Files", "text/uri-list", "text/plain"].includes(type),
              )) ||
            (acceptsCanvasMediaVaultDrop(component) &&
              transferTypes.includes(CANVAS_MEDIA_VAULT_DRAG_TYPE))
          )
        ) {
          event.preventDefault();
        }
      }}
      onDrop={(event) => {
        if (!isEditing) return;
        if (
          Array.from(event.dataTransfer?.types || []).includes(
            CANVAS_MEDIA_VAULT_DRAG_TYPE,
          )
        ) {
          event.preventDefault();
          event.stopPropagation();
          if (!acceptsCanvasMediaVaultDrop(component)) return;
          const mediaAsset = readCanvasMediaVaultPayload(event.dataTransfer);
          if (!mediaAsset) return;
          if (component.type === "svg_graphic_block" && mediaAsset.svgMarkup) {
            onUpdate(component.id, "imageSettings", null);
            onUpdate(component.id, "svgMarkup", mediaAsset.svgMarkup);
            return;
          }
          if (!safeImageDataUrl(mediaAsset.dataUrl)) return;
          if (component.type === "svg_graphic_block") {
            onUpdate(component.id, "svgMarkup", "");
          }
          onUpdate(component.id, "imageSettings", {
            dataUrl: mediaAsset.dataUrl,
            sourceUrl: "",
            alt: mediaAsset.name || component.label || "Canvas image",
          });
          return;
        }
        if (acceptsCanvasImageAsset(component)) {
          const file = event.dataTransfer.files?.[0];
          if (file) {
            event.preventDefault();
            event.stopPropagation();
            onUploadImage(component.id, file);
            return;
          }
          const droppedUrl = (
            event.dataTransfer.getData("text/uri-list") ||
            event.dataTransfer.getData("text/plain") ||
            ""
          ).split(/\r?\n/).find((value) => value && !value.startsWith("#")) || "";
          if (safeCanvasAssetUrl(droppedUrl)) {
            event.preventDefault();
            event.stopPropagation();
            onUpdate(component.id, "imageSettings", {
              dataUrl: "",
              sourceUrl: droppedUrl,
              alt: component.imageSettings?.alt || component.label || "Canvas image",
            });
            return;
          }
        }
        onDrop(event, component.id, parentId);
      }}
      onMouseUp={commitNativeResize}
      onTouchEnd={commitNativeResize}
      style={{
        ...blockVisualStyle(component, layoutMode, gridColumns),
        ...(isEditing
          ? {
              resize: "both",
              overflow: "auto",
              minWidth: 120,
              minHeight: 80,
            }
          : {}),
      }}
      tabIndex={canEdit ? 0 : undefined}
    >
      {isEditing && component.type === "navigation_link_menu_bar" && (
        <button
          aria-label="Configure Navigation Link Menu Bar"
          className="dynamic-mesh-block__editor dynamic-mesh-block__settings-toggle"
          onClick={(event) => {
            event.stopPropagation();
            onSelect(component.id);
          }}
          title="Open menu link settings"
          type="button"
        >
          ⚙
        </button>
      )}
      {isEditing && isSelected && (
        <div
          aria-label={`${component.label || "Canvas block"} inline editor`}
          className="dynamic-mesh-block__editor"
          onClick={(event) => event.stopPropagation()}
          role="region"
        >
          <div className="dynamic-mesh-block__editor-heading">
            <span>Inline block editor</span>
            <button
              aria-label={`Remove ${component.label || "canvas block"}`}
              onClick={() => onRemove(component.id)}
              type="button"
            >
              Remove
            </button>
          </div>
          <label className="dynamic-mesh-block__editor-field">
            Block title
            <input
              aria-label="Block title"
              maxLength={120}
              onChange={(event) =>
                onUpdate(component.id, "label", event.target.value)
              }
              required
              value={component.label || ""}
            />
          </label>
          <label className="dynamic-mesh-block__editor-field">
            Text content
            <textarea
              aria-label="Text content"
              maxLength={2_000}
              onChange={(event) =>
                onUpdate(component.id, "content", event.target.value)
              }
              rows={3}
              value={component.content || ""}
            />
          </label>
          {isMasterOverviewContainer && (
            <fieldset className="master-overview-container__layout-controls">
              <legend>Nested layout</legend>
              <label className="dynamic-mesh-block__editor-field">
                Inner layout mode
                <select
                  aria-label="Inner layout mode"
                  onChange={(event) =>
                    onUpdate(component.id, "childLayoutMode", event.target.value)
                  }
                  value={childLayoutMode}
                >
                  <option value="grid">Grid</option>
                  <option value="absolute">Free position</option>
                </select>
              </label>
              <CanvasNumberField
                label="Inner columns"
                max={12}
                min={1}
                onCommit={(value) =>
                  onUpdate(component.id, "childGridColumns", value)
                }
                value={childGridColumns}
              />
              <CanvasNumberField
                label="Inner gap"
                max={40}
                min={0}
                onCommit={(value) => onUpdate(component.id, "childGap", value)}
                value={childGap}
              />
              <CanvasNumberField
                label="Inner canvas height"
                max={1_600}
                min={240}
                onCommit={(value) =>
                  onUpdate(component.id, "childCanvasHeight", value)
                }
                value={childCanvasHeight}
              />
            </fieldset>
          )}
          {component.type === "rich_text_block" && (
            <label className="dynamic-mesh-block__editor-field">
              Heading level
              <select
                aria-label="Heading level"
                onChange={(event) =>
                  onUpdate(component.id, "headingLevel", Number(event.target.value))
                }
                value={component.headingLevel || 2}
              >
                <option value={1}>H1</option>
                <option value={2}>H2</option>
                <option value={3}>H3</option>
              </select>
            </label>
          )}
          {component.type === "video_embed_block" && (
            <label className="dynamic-mesh-block__editor-field">
              Video URL (HTTPS MP4 or YouTube)
              <input
                aria-label="Video URL"
                maxLength={2_048}
                onChange={(event) =>
                  onUpdate(component.id, "href", event.target.value)
                }
                placeholder="https://…/clip.mp4 or YouTube URL"
                type="text"
                value={component.href || ""}
              />
            </label>
          )}
          {component.type === "secure_link_anchor" && (
            <label className="dynamic-mesh-block__editor-field">
              Hyperlink destination
              <input
                aria-label="Hyperlink destination"
                maxLength={2_048}
                onChange={(event) =>
                  onUpdate(component.id, "href", event.target.value)
                }
                placeholder="/same-site-path or https://…"
                type="text"
                value={component.href || ""}
              />
            </label>
          )}
          {component.type === "svg_graphic_block" && (
            <label className="dynamic-mesh-block__editor-field dynamic-mesh-block__editor-field--wide">
              Safe SVG graphic
              <textarea
                aria-label="SVG graphics markup"
                maxLength={12_000}
                onChange={(event) =>
                  onUpdate(component.id, "svgMarkup", event.target.value)
                }
                placeholder='<svg viewBox="0 0 100 100"><circle cx="50" cy="50" r="36" fill="#8F5C38"/></svg>'
                rows={6}
                value={component.svgMarkup || ""}
              />
            </label>
          )}
          <div className="dynamic-mesh-block__editor-grid">
            <CanvasNumberField
              label="Font size"
              max={48}
              min={8}
              onCommit={(value) =>
                onUpdateStyles(component.id, "fontSize", value)
              }
              value={styles.fontSize || 14}
            />
            <label className="dynamic-mesh-block__editor-field">
              Font family
              <select
                aria-label="Font family"
                onChange={(event) =>
                  onUpdateStyles(component.id, "fontFamily", event.target.value)
                }
                value={styles.fontFamily || "system"}
              >
                {STUDIO_FONT_KEYS.map((fontKey) => {
                  const theme = STUDIO_THEMES.find(
                    (item) => item.fontKey === fontKey,
                  );
                  return (
                    <option key={fontKey} value={fontKey}>
                      {theme?.fontFamily || {
                        system: "System",
                        serif: "Serif",
                        "sans-serif": "Sans serif",
                        monospace: "Monospace",
                      }[fontKey] || fontKey}
                    </option>
                  );
                })}
              </select>
            </label>
            <label className="dynamic-mesh-block__editor-field">
              Font weight
              <select
                aria-label="Font weight"
                onChange={(event) =>
                  onUpdateStyles(component.id, "fontWeight", Number(event.target.value))
                }
                value={styles.fontWeight || 400}
              >
                {[300, 400, 500, 600, 700, 800, 900].map((weight) => (
                  <option key={weight} value={weight}>{weight}</option>
                ))}
              </select>
            </label>
            <label className="dynamic-mesh-block__editor-field">
              Text alignment
              <select
                aria-label="Text alignment"
                onChange={(event) =>
                  onUpdateStyles(component.id, "textAlign", event.target.value)
                }
                value={styles.textAlign || "left"}
              >
                <option value="left">Left</option>
                <option value="center">Center</option>
                <option value="right">Right</option>
              </select>
            </label>
            {layoutMode === "absolute" ? (
              <>
                <CanvasNumberField
                  label="X position"
                  max={100}
                  min={0}
                  onCommit={(value) => onUpdate(component.id, "x", value)}
                  value={component.x ?? 0}
                />
                <CanvasNumberField
                  label="Y position"
                  max={100}
                  min={0}
                  onCommit={(value) => onUpdate(component.id, "y", value)}
                  value={component.y ?? 0}
                />
                <CanvasNumberField
                  label="Width percentage"
                  max={100}
                  min={1}
                  onCommit={(value) => onUpdate(component.id, "width", value)}
                  value={component.width ?? 50}
                />
                <CanvasNumberField
                  label="Height percentage"
                  max={100}
                  min={1}
                  onCommit={(value) => onUpdate(component.id, "height", value)}
                  value={component.height ?? 24}
                />
              </>
            ) : (
              <>
                <CanvasNumberField
                  label="Column span"
                  max={gridColumns}
                  min={1}
                  onCommit={(value) =>
                    onUpdate(component.id, "gridColumnSpan", value)
                  }
                  value={component.gridColumnSpan || 1}
                />
                <CanvasNumberField
                  label="Row span"
                  max={30}
                  min={1}
                  onCommit={(value) =>
                    onUpdate(component.id, "gridRowSpan", value)
                  }
                  value={component.gridRowSpan || 1}
                />
                <CanvasNumberField
                  label="Block width in pixels"
                  max={1_440}
                  min={120}
                  onCommit={(value) =>
                    onUpdateStyles(component.id, "widthPx", value)
                  }
                  value={styles.widthPx || 360}
                />
                <CanvasNumberField
                  label="Block height in pixels"
                  max={1_600}
                  min={80}
                  onCommit={(value) =>
                    onUpdateStyles(component.id, "heightPx", value)
                  }
                  value={styles.heightPx || 180}
                />
              </>
            )}
          </div>
          {isButton && (
            <fieldset className="dynamic-mesh-block__binding-editor">
              <legend>Button function binding</legend>
              <label className="dynamic-mesh-block__editor-field">
                Core utility
                <select
                  aria-label="Button function binding"
                  onChange={(event) =>
                    onUpdate(component.id, "functionBinding", event.target.value)
                  }
                  value={actionBinding}
                >
                  <option value="">Choose a utility</option>
                  <option value="REFRESH_LAYOUT">Refresh layout</option>
                  {(roleKey === "principal" || context.user?.role === "MASTER_ADMIN") && (
                    <option value="EXPORT_ROSTER_DATA">Export roster CSV</option>
                  )}
                  <option value="LAUNCH_EXTERNAL_HYPERLINK">Launch HTTPS link</option>
                   <option value="TRIGGER_OTP_GATE">Trigger classroom OTP gate</option>
                   <option value="ROLLOVER_PAST_PACING">Rollover past pacing</option>
                </select>
              </label>
              {actionBinding === "LAUNCH_EXTERNAL_HYPERLINK" && (
                <label className="dynamic-mesh-block__editor-field">
                  Link destination
                  <input
                    aria-label="Link destination"
                    maxLength={2_048}
                    onChange={(event) =>
                      onUpdate(component.id, "href", event.target.value)
                    }
                    placeholder="/same-site-path or https://…"
                    type="text"
                    value={component.href || ""}
                  />
                </label>
              )}
            </fieldset>
          )}
          {acceptsCanvasImageAsset(component) && (
            <div className="dynamic-mesh-block__image-editor">
              <label className="dynamic-mesh-block__editor-field">
                Image asset (PNG, JPEG, or WebP; 180 KB max)
                <input
                  accept="image/png,image/jpeg,image/webp"
                  aria-label="Image asset file"
                  onChange={(event) => onUploadImage(component.id, event)}
                  type="file"
                />
              </label>
                <label className="dynamic-mesh-block__editor-field">
                  Image source URL or same-site path
                  <input
                    aria-label="Image source URL or same-site path"
                    maxLength={2_048}
                    onChange={(event) =>
                      onUpdate(component.id, "imageSettings", {
                        dataUrl: "",
                        sourceUrl: event.target.value,
                        alt: component.imageSettings?.alt || "Canvas image",
                      })
                    }
                    placeholder="https://… or /assets/image.webp"
                    value={component.imageSettings?.sourceUrl || ""}
                  />
                </label>
              <label className="dynamic-mesh-block__editor-field">
                Image description
                <input
                  aria-label="Image description"
                  maxLength={240}
                  onChange={(event) =>
                    onUpdate(component.id, "imageSettings", {
                      dataUrl: imageData || "",
                        sourceUrl: component.imageSettings?.sourceUrl || "",
                      alt: event.target.value,
                    })
                  }
                  value={component.imageSettings?.alt || ""}
                />
              </label>
            </div>
          )}
          <div className="dynamic-mesh-block__editor-actions">
            <button
              disabled={index === 0}
              onClick={() => onMergePrevious(component.id)}
              type="button"
            >
              Merge with previous
            </button>
            {component.groupId && (
              <button onClick={() => onUngroup(component.id)} type="button">
                Ungroup
              </button>
            )}
          </div>
        </div>
      )}
      {!isMasterOverviewContainer && (
        <span className="dynamic-mesh-block__eyebrow">
          {component.type.replaceAll("_", " ")}
        </span>
      )}
      {isMasterOverviewContainer ? (
        <header className="master-overview-container__header">
          <span>Master Overview</span>
        </header>
      ) : headingLevel === 1 ? (
        <h1 className="dynamic-mesh-block__title dynamic-mesh-block__title--heading">
          {component.content || component.label || "Section heading"}
        </h1>
      ) : headingLevel === 2 ? (
        <h2 className="dynamic-mesh-block__title dynamic-mesh-block__title--heading">
          {component.content || component.label || "Section heading"}
        </h2>
      ) : headingLevel === 3 ? (
        <h3 className="dynamic-mesh-block__title dynamic-mesh-block__title--heading">
          {component.content || component.label || "Section heading"}
        </h3>
      ) : (
        <strong className="dynamic-mesh-block__title">
          {component.label || component.type}
        </strong>
      )}
      {component.type === "navigation_link_menu_bar" && (
        <nav
          aria-label={component.label || "Navigation link menu"}
          className="canvas-navigation-menu"
        >
          {(component.menuItems || []).map((item) => {
            const targetTab = subTabs.find((tab) => tab.tabId === item.targetTabId);
            if (!targetTab) return null;
            return (
              <a
                aria-current={targetTab.tabId === activeSubTabId ? "page" : undefined}
                href={`#/tab/${encodeURIComponent(targetTab.tabId)}`}
                key={item.id}
                onClick={(event) => {
                  event.preventDefault();
                  event.stopPropagation();
                  if (isEditing) {
                    onSelect(component.id);
                  } else {
                    onNavigateTab?.(targetTab.tabId);
                  }
                }}
              >
                {item.label || targetTab.tabLabelDisplayName}
              </a>
            );
          })}
        </nav>
      )}
      {isMasterOverviewContainer && (
        <div className="master-overview-container__frame">
          <div
            aria-label="Master Overview nested component layout"
            className={`dynamic-mesh-layout is-${childLayoutMode}`}
            data-canvas-nested-layout={component.id}
            onDragOver={(event) => {
              if (isEditing) event.preventDefault();
            }}
            onDrop={(event) => {
              if (isEditing) onDrop(event, null, component.id);
            }}
            style={{
              "--dynamic-mesh-columns": childGridColumns,
              "--dynamic-mesh-gap": `${childGap}px`,
              "--dynamic-mesh-height": `${childCanvasHeight}px`,
              gridTemplateColumns:
                childLayoutMode === "grid"
                  ? `repeat(${childGridColumns}, minmax(0, 1fr))`
                  : undefined,
            }}
          >
            {childComponents.map((child, childIndex) => (
              <DynamicMeshBlock
                addToolboxComponent={addToolboxComponent}
                activeSubTabId={activeSubTabId}
                canEdit={canEdit}
                component={child}
                context={context}
                gridColumns={childGridColumns}
                index={childIndex}
                isEditing={isEditing}
                isSelected={selectedComponentId === child.id}
                key={child.id || `${child.type}-${childIndex}`}
                layoutMode={childLayoutMode}
                onDragEnd={onDragEnd}
                onDragStart={onDragStart}
                onDrop={(event, targetId, destinationParentId) =>
                  onDrop(
                    event,
                    targetId,
                    destinationParentId || component.id,
                  )
                }
                onNavigateTab={onNavigateTab}
                onMergePrevious={(componentId, childParentId) =>
                  onMergePrevious(componentId, childParentId || component.id)
                }
                onRefresh={onRefresh}
                onRemove={onRemove}
                onResize={onResize}
                onSelect={onSelect}
                onUngroup={(componentId, childParentId) =>
                  onUngroup(componentId, childParentId || component.id)
                }
                onUpdate={onUpdate}
                onUpdateStyles={onUpdateStyles}
                onUploadImage={onUploadImage}
                parentId={component.id}
                roleKey={roleKey}
                selectedComponentId={selectedComponentId}
                subTabs={subTabs}
              />
            ))}
            {isEditing && childComponents.length === 0 && (
              <p className="master-overview-container__empty">
                Drop components here
              </p>
            )}
          </div>
        </div>
      )}
      {imageSource && (
        <img
          alt={component.imageSettings?.alt || ""}
          className="dynamic-mesh-block__image"
          src={imageSource}
        />
      )}
      {component.type === "image_asset" && !imageSource && (
        <p className="dynamic-mesh-block__note">
          {isEditing ? "Select this block to add an image." : "No image asset is attached."}
        </p>
      )}
      {videoSource?.kind === "mp4" && (
        <video
          className="dynamic-mesh-video"
          controls
          playsInline
          preload="metadata"
          src={videoSource.src}
        />
      )}
      {videoSource?.kind === "youtube" && (
        <div className="dynamic-mesh-video-frame">
          <iframe
            allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
            allowFullScreen
            referrerPolicy="strict-origin-when-cross-origin"
            sandbox="allow-scripts allow-same-origin allow-presentation"
            src={getYouTubeEmbedUrl(videoUrl)}
            title={component.content || component.label || "Embedded video"}
          />
        </div>
      )}
      {component.type === "video_embed_block" && !videoSource && (
        <p className="dynamic-mesh-block__note">
          {isEditing
            ? "Add an HTTPS MP4 URL or a YouTube video URL."
            : "No supported video URL is configured."}
        </p>
      )}
      {component.type === "secure_link_anchor" &&
        !safeInternalOrHttpsUrl(component.href) && (
          <p className="dynamic-mesh-block__note">
            {isEditing
              ? "Add a same-site path or HTTPS destination."
              : "No safe link destination is configured."}
          </p>
        )}
      {svgImageSource && (
        <img
          alt={component.content || component.label || "SVG graphic"}
          className="dynamic-mesh-block__image dynamic-mesh-svg"
          src={svgImageSource}
        />
      )}
      {component.type === "svg_graphic_block" && !svgImageSource && (
        <p className="dynamic-mesh-block__note">
          {isEditing
            ? "Add static SVG shapes. Scripts, styles, links, and external references are not allowed."
            : "No safe SVG graphic is configured."}
        </p>
      )}
      {component.content && !headingLevel && (
        <p className="dynamic-mesh-block__content">{component.content}</p>
      )}
      {component.nodeDefinition === "ELibraryDataNode" && (
        <section
          aria-label="E-Library metadata execution"
          className="dynamic-mesh-library-binding"
        >
          {!hasExecutableLibraryBindings ? (
            <p className="dynamic-mesh-block__note">
              This E-Library node is not fully configured.
            </p>
          ) : context.user?.role !== "SCHOLAR" ? (
            <p className="dynamic-mesh-block__note">
              Configured and idle. Run this node from an authenticated scholar session.
            </p>
          ) : (
            <>
              <button
                className="dynamic-mesh-block__action"
                disabled={libraryExecutionState === "running"}
                onClick={(event) => {
                  event.stopPropagation();
                  void executeLibraryBinding();
                }}
                type="button"
              >
                {libraryExecutionState === "running"
                  ? "Loading approved metadata…"
                  : "Load approved library metadata"}
              </button>
              <p aria-live="polite" className="dynamic-mesh-block__note" role="status">
                {libraryExecutionState === "idle" &&
                  "Configured and idle. Nothing is running yet."}
                {libraryExecutionState === "running" && "Loading approved metadata…"}
                {libraryExecutionState === "completed" &&
                  `${libraryAssets.length} approved asset${libraryAssets.length === 1 ? "" : "s"} loaded for your grade phase.`}
                {libraryExecutionState === "error" &&
                  `Metadata load failed: ${libraryExecutionError}`}
              </p>
              {libraryAssets.length > 0 && (
                <ul className="dynamic-mesh-library-binding__assets">
                  {libraryAssets.map((asset) => (
                    <li key={asset.asset_id}>
                      <strong>{asset.title}</strong>
                      <span>{asset.author} · {asset.genre_classification}</span>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      )}
      {variableValue && (
        <output
          className="dynamic-mesh-block__binding"
          data-variable-binding={component.variableBinding}
        >
          {variableValue}
        </output>
      )}
      {component.dataBinding && (
        <small className="dynamic-mesh-block__subject">
          Subject link · {component.dataBinding}
        </small>
      )}
      {component.type === "psychometric_input_field" && (
        <label className="dynamic-mesh-block__field">
          <span>Response</span>
          <input
            onChange={(event) => setLocalValue(event.target.value)}
            value={localValue}
          />
        </label>
      )}
      {component.type === "dynamic_assessment_selector" && (
        <label className="dynamic-mesh-block__field">
          <span>Choose an option</span>
          <select
            onChange={(event) => setLocalValue(event.target.value)}
            value={localValue}
          >
            <option value="">Select</option>
            {(component.options || []).map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </label>
      )}
      {component.type === "source_document_uploader_module" && (
        <p className="dynamic-mesh-block__note">
          Document upload needs a configured destination.
        </p>
      )}
      {component.type === "secure_link_anchor" &&
        safeInternalOrHttpsUrl(component.href) && (
          <a className="dynamic-mesh-block__link" href={component.href}>
            {component.content || "Open secure resource"}
          </a>
        )}
      {(isButton || actionBinding) && (
        <button
          className="dynamic-mesh-block__action"
          disabled={
            (isButton && !actionBinding) ||
            actionBinding === "EXPORT_ROSTER_DATA" &&
            (roleKey !== "principal" || context.user?.role !== "PRINCIPAL") ||
            (actionBinding === "LAUNCH_EXTERNAL_HYPERLINK" &&
              !safeInternalOrHttpsUrl(component.href))
          }
          onClick={runBoundAction}
          type="button"
        >
          {actionBinding === "LAUNCH_EXTERNAL_HYPERLINK"
            ? component.label || "Open link"
            : actionBinding === "EXPORT_ROSTER_DATA"
            ? "Export roster CSV"
            : actionBinding === "ACKNOWLEDGE_TELEMETRY_ALERT"
              ? "Acknowledgement unavailable"
              : actionBinding === "UNLOCK_SIMULATION_CANVAS"
                ? "Unlock simulation"
                : actionBinding === "REFRESH_LAYOUT"
                  ? "Refresh layout"
                  : "Unbound button"}
        </button>
      )}
      {actionMessage && (
        <small aria-live="polite" className="dynamic-mesh-block__action-message">
          {actionMessage}
        </small>
      )}
      <span
        className="dynamic-mesh-block__font"
        style={{
          fontSize: styles.fontSize || 14,
          fontWeight: styles.fontWeight || 400,
        }}
      >
        {styles.fontFamily || "system"} · {styles.textAlign || "left"}
      </span>
    </article>
  );
}

function blocksWithGroups(components) {
  const groups = [];
  const groupIndexes = new Map();
  components.forEach((component, index) => {
    if (!component.groupId) {
      groups.push({ id: component.id || `block-${index}`, components: [component] });
      return;
    }
    if (!groupIndexes.has(component.groupId)) {
      groupIndexes.set(component.groupId, groups.length);
      groups.push({ id: component.groupId, components: [] });
    }
    groups[groupIndexes.get(component.groupId)].components.push(component);
  });
  return groups;
}

const MIN_CANVAS_GRID_ORDER = -1_000;
const MAX_CANVAS_GRID_ORDER = 1_000;

function validCanvasGridOrder(value) {
  return (
    Number.isInteger(value) &&
    value >= MIN_CANVAS_GRID_ORDER &&
    value <= MAX_CANVAS_GRID_ORDER
  );
}

export function orderCanvasComponents(components) {
  return components
    .map((component, sourceIndex) => ({
      component,
      sourceIndex,
      gridOrderIndex: validCanvasGridOrder(component.gridOrderIndex)
        ? component.gridOrderIndex
        : sourceIndex,
    }))
    .sort(
      (left, right) =>
        left.gridOrderIndex - right.gridOrderIndex ||
        left.sourceIndex - right.sourceIndex,
    )
    .map(({ component }) => component);
}

const DEFAULT_INLINE_VISIBILITY = {
  master_admin: true,
  principal: true,
  grade_hod: true,
  educator: true,
  parent: true,
  scholar: true,
};
const CanvasSubTabContext = createContext(null);

function normalizeSubTabs(layout) {
  const source = Array.isArray(layout?.subTabs) ? layout.subTabs : [];
  const defaults = {
    layoutMode: layout?.layoutMode === "absolute" ? "absolute" : "grid",
    gridColumns: clamp(layout?.gridColumns, 1, 12, 12),
    gridRows: clamp(layout?.gridRows, 1, 30, 1),
    gap: clamp(layout?.gap, 0, 40, 32),
    canvasHeight: clamp(layout?.canvasHeight, 240, 1_600, 480),
  };
  const tabs = source
    .filter((tab) => tab && typeof tab.tabId === "string")
    .map((tab, index) => ({
      tabId: tab.tabId,
      tabLabelDisplayName: String(tab.tabLabelDisplayName || `Canvas Tab ${index + 1}`).slice(0, 120),
      displayOrderWeight: Number.isFinite(tab.displayOrderWeight) ? tab.displayOrderWeight : index,
      status: tab.status === "active" ? "active" : "active",
      layoutMode: tab.layoutMode === "absolute" ? "absolute" : tab.layoutMode === "grid" ? "grid" : defaults.layoutMode,
      gridColumns: clamp(tab.gridColumns ?? defaults.gridColumns, 1, 12, defaults.gridColumns),
      gridRows: clamp(tab.gridRows ?? defaults.gridRows, 1, 30, defaults.gridRows),
      gap: clamp(tab.gap ?? defaults.gap, 0, 40, defaults.gap),
      canvasHeight: clamp(tab.canvasHeight ?? defaults.canvasHeight, 240, 1_600, defaults.canvasHeight),
      canvasLayout: Array.isArray(tab.canvasLayout) ? tab.canvasLayout : [],
    }))
    .sort((a, b) => a.displayOrderWeight - b.displayOrderWeight);
  if (!tabs.length) {
    tabs.push({
      tabId: "master-overview",
      tabLabelDisplayName: "Master Overview",
      displayOrderWeight: 0,
      status: "active",
      ...defaults,
      canvasLayout: Array.isArray(layout?.canvasLayout) ? layout.canvasLayout : [],
    });
  }
  const activeId = tabs.some((tab) => tab.tabId === layout?.activeSubTabId)
    ? layout.activeSubTabId
    : tabs[0].tabId;
  return { tabs, activeId, activeTab: tabs.find((tab) => tab.tabId === activeId) || tabs[0] };
}

function CanvasStudioEditorDrawer({ area }) {
  const handlers = area.handlers;
  const toolboxItems = PROFILE_COMPONENT_TOOLBOX_ITEMS.filter(
    (item) =>
      !item.principalOnly ||
      area.handlers.dashboardViewport === "Principal Overview Dashboard",
  );
  const selectedComponent = area.visibleComponents.find(
    (component) => component.id === handlers.selectedComponentId,
  );
  const selectedColor =
    normalizeHexColor(selectedComponent?.styles?.color) || "#302B27";
  const [colorDraft, setColorDraft] = useState(selectedColor);
  const [mediaAssets, setMediaAssets] = useState([]);
  const [mediaVaultMessage, setMediaVaultMessage] = useState("");
  const [mediaVaultHasError, setMediaVaultHasError] = useState(false);
  const [isAddingMediaAssets, setIsAddingMediaAssets] = useState(false);
  const mediaFileInputRef = useRef(null);
  const mediaAssetCounterRef = useRef(0);

  useEffect(() => {
    setColorDraft(selectedColor);
  }, [selectedComponent?.id, selectedColor]);

  function updateSelectedColor(value) {
    setColorDraft(value);
    const color = normalizeHexColor(value);
    if (selectedComponent && color) {
      handlers.updateComponentStyles(selectedComponent.id, "color", color);
    }
  }

  function addToolboxItem(item) {
    handlers.addToolboxComponent(item.id);
  }

  function startToolboxDrag(event, item) {
    if (!area.isEditing || !event.dataTransfer) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(PROFILE_TOOLBOX_DRAG_TYPE, item.id);
    event.dataTransfer.setData("text/plain", item.label);
  }

  async function addMediaVaultFiles(files) {
    if (!area.isEditing || isAddingMediaAssets) return;
    const availableSlots = CANVAS_MEDIA_VAULT_MAX_ITEMS - mediaAssets.length;
    if (availableSlots <= 0) {
      setMediaVaultHasError(true);
      setMediaVaultMessage("The Media Gallery Vault is full. Remove an item before uploading more.");
      return;
    }
    if (!files.length) return;

    setIsAddingMediaAssets(true);
    setMediaVaultHasError(false);
    setMediaVaultMessage("Reading local image files…");
    const addedAssets = [];
    const errors = [];
    try {
      for (const file of files.slice(0, availableSlots)) {
        const id = `media-${Date.now().toString(36)}-${++mediaAssetCounterRef.current}`;
        try {
          addedAssets.push(await readCanvasMediaVaultFile(file, id));
        } catch (error) {
          errors.push(
            error instanceof Error
              ? error.message
              : "A selected image could not be read.",
          );
        }
      }
      if (addedAssets.length) {
        setMediaAssets((current) =>
          [...current, ...addedAssets].slice(0, CANVAS_MEDIA_VAULT_MAX_ITEMS),
        );
      }
      const overflowCount = Math.max(0, files.length - availableSlots);
      if (errors.length || overflowCount) {
        setMediaVaultHasError(true);
        setMediaVaultMessage(
          [
            addedAssets.length
              ? `${addedAssets.length} image${addedAssets.length === 1 ? "" : "s"} added.`
              : "",
            ...errors.slice(0, 3),
            overflowCount ? "The vault holds up to 16 images." : "",
          ]
            .filter(Boolean)
            .join(" "),
        );
      } else if (addedAssets.length) {
        setMediaVaultHasError(false);
        setMediaVaultMessage(
          `${addedAssets.length} image${addedAssets.length === 1 ? "" : "s"} ready to drag onto the canvas.`,
        );
      }
    } finally {
      setIsAddingMediaAssets(false);
    }
  }

  function startMediaVaultDrag(event, asset) {
    if (!area.isEditing || !event.dataTransfer) {
      event.preventDefault();
      return;
    }
    try {
      event.dataTransfer.effectAllowed = "copy";
      event.dataTransfer.setData(
        CANVAS_MEDIA_VAULT_DRAG_TYPE,
        JSON.stringify({
          version: 1,
          name: asset.name,
          dataUrl: asset.dataUrl,
          svgMarkup: asset.svgMarkup,
        }),
      );
      event.dataTransfer.setData("text/plain", asset.name);
    } catch {
      event.preventDefault();
      setMediaVaultHasError(true);
      setMediaVaultMessage("This browser could not start the image drag.");
    }
  }

  const categories = Array.from(
    new Set(toolboxItems.map((item) => item.category)),
  );

  return (
    <aside
      aria-label="Canvas Project Studio editor"
      className="canvas-studio-editor"
      data-canvas-editor-ui="true"
      role="region"
    >
      <div className="canvas-studio-editor__heading">
        <div>
          <span>Canvas Project Studio</span>
          <strong>{selectedComponent?.label || "Component toolbox"}</strong>
        </div>
      </div>
      <details className="native-canvas-toolbox canvas-studio-editor__toolbox" open>
        <summary>🧰 Component Toolbox Shelf</summary>
        <p>
          {area.isEditing
            ? "Drag a component into a canvas slot, or select it to add."
            : "Enter Studio Edit Mode to add components."}
        </p>
        <div className="native-canvas-toolbox__scroll">
          {categories.map((category) => (
            <details
              aria-label={category}
              className="native-canvas-toolbox__category"
              key={category}
            >
              <summary>{category}</summary>
              <div className="native-canvas-toolbox__grid">
                {toolboxItems
                  .filter((item) => item.category === category)
                  .map((item) => (
                    <button
                      aria-label={`Add ${item.label} to canvas`}
                      className="native-canvas-toolbox__item"
                      data-canvas-editor-ui="true"
                      disabled={!area.isEditing}
                      draggable={area.isEditing}
                      key={item.id}
                      onClick={() => addToolboxItem(item)}
                      onDragStart={(event) => startToolboxDrag(event, item)}
                      type="button"
                    >
                      <span>{item.label}</span>
                      {item.premium && (
                        <small className="navigation-menu-premium-badge">
                          Premium
                        </small>
                      )}
                    </button>
                  ))}
              </div>
            </details>
          ))}
        </div>
      </details>

      <details
        aria-label="Media Gallery Vault"
        className="native-canvas-toolbox__category canvas-media-vault"
        data-canvas-editor-ui="true"
      >
        <summary>🖼️ Media Gallery Vault</summary>
        <div style={{ display: "grid", gap: "0.65rem", padding: "0.75rem" }}>
          <p style={{ margin: 0 }}>
            Add local PNG, JPG, or SVG graphics, then drag a thumbnail onto an
            image, vector, or card block. Uploaded files stay in this editor
            session; placed images are saved with the layout.
          </p>
          <input
            accept="image/png,image/jpeg,image/svg+xml,.png,.jpg,.jpeg,.svg"
            aria-label="Brand graphic files"
            disabled={
              !area.isEditing ||
              isAddingMediaAssets ||
              mediaAssets.length >= CANVAS_MEDIA_VAULT_MAX_ITEMS
            }
            multiple
            onChange={(event) => {
              const files = Array.from(event.currentTarget.files || []);
              event.currentTarget.value = "";
              void addMediaVaultFiles(files);
            }}
            ref={mediaFileInputRef}
            style={{ display: "none" }}
            type="file"
          />
          <button
            className="native-canvas-toolbox__item"
            data-canvas-editor-ui="true"
            disabled={
              !area.isEditing ||
              isAddingMediaAssets ||
              mediaAssets.length >= CANVAS_MEDIA_VAULT_MAX_ITEMS
            }
            onClick={() => mediaFileInputRef.current?.click()}
            style={{
              background: "#171717",
              border: "1px solid #171717",
              color: "#FFFFFF",
              fontWeight: 800,
              justifyContent: "center",
              minHeight: "2.8rem",
              width: "100%",
            }}
            type="button"
          >
            {isAddingMediaAssets ? "Reading local files…" : "➕ Upload Brand Graphic"}
          </button>
          <small>
            Up to 16 files. PNG/JPG: 180 KB max. SVG: 12 KB max; unsafe SVG
            elements and links are rejected.
          </small>
          {mediaVaultMessage && (
            <p
              aria-live="polite"
              role={mediaVaultHasError ? "alert" : "status"}
              style={{ margin: 0 }}
            >
              {mediaVaultMessage}
            </p>
          )}
          {mediaAssets.length ? (
            <div
              aria-label="Uploaded brand graphics"
              style={{
                display: "grid",
                gap: "0.6rem",
                gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
              }}
            >
              {mediaAssets.map((asset) => (
                <div
                  key={asset.id}
                  style={{
                    border: "1px solid #C8BDB1",
                    borderRadius: "0.75rem",
                    display: "grid",
                    gap: "0.35rem",
                    minWidth: 0,
                    padding: "0.35rem",
                  }}
                >
                  <button
                    aria-label={`Drag ${asset.name} to the canvas`}
                    className="native-canvas-toolbox__item"
                    data-canvas-editor-ui="true"
                    disabled={!area.isEditing}
                    draggable={area.isEditing}
                    onDragStart={(event) => startMediaVaultDrag(event, asset)}
                    style={{
                      display: "grid",
                      gap: "0.35rem",
                      justifyItems: "stretch",
                      minWidth: 0,
                      padding: "0.25rem",
                      textAlign: "left",
                    }}
                    title={`Drag ${asset.name} onto a canvas image or card block`}
                    type="button"
                  >
                    <img
                      alt=""
                      src={asset.previewUrl}
                      style={{
                        aspectRatio: "1 / 1",
                        background: "#F1ECE5",
                        borderRadius: "0.5rem",
                        objectFit: "cover",
                        width: "100%",
                      }}
                    />
                    <span
                      style={{
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {asset.name}
                    </span>
                    <small>Drag to canvas</small>
                  </button>
                  <button
                    aria-label={`Remove ${asset.name} from Media Gallery Vault`}
                    disabled={!area.isEditing}
                    onClick={() =>
                      setMediaAssets((current) =>
                        current.filter((item) => item.id !== asset.id),
                      )
                    }
                    type="button"
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <p style={{ margin: 0 }}>Uploaded graphics will appear here.</p>
          )}
        </div>
      </details>

      {typeof area.onStandaloneExport === "function" && (
        <section
          aria-label="Canvas project bundle export"
          className="canvas-studio-editor__properties canvas-studio-editor__export"
        >
          <h3>Independent app bundle</h3>
          <button
            aria-busy={area.standaloneExportState === "loading"}
            className="canvas-studio-editor__export-button"
            disabled={area.standaloneExportState === "loading"}
            onClick={() => void area.onStandaloneExport()}
            type="button"
          >
            {area.standaloneExportState === "loading"
              ? "Preparing independent app bundle…"
              : "📦 Compile & Download Independent App Bundle (.zip)"}
          </button>
          {area.standaloneExportMessage && (
            <p
              aria-live="polite"
              className={`canvas-studio-editor__export-status is-${area.standaloneExportState}`}
              role={area.standaloneExportState === "error" ? "alert" : "status"}
            >
              {area.standaloneExportMessage}
            </p>
          )}
        </section>
      )}

      {selectedComponent ? (
        <div className="canvas-studio-editor__properties">
          <h3>Selected block properties</h3>
          <label className="dynamic-mesh-block__editor-field">
            Block title
            <input
              aria-label="Studio block title"
              disabled={!area.isEditing}
              maxLength={120}
              onChange={(event) =>
                handlers.updateComponent(
                  selectedComponent.id,
                  "label",
                  event.target.value,
                )
              }
              value={selectedComponent.label || ""}
            />
          </label>

          <div className="dynamic-mesh-block__editor-field">
            <span className="native-canvas-editor__field-label">Text color</span>
            <span className="native-canvas-editor__color-input">
              <span
                aria-hidden="true"
                className="native-canvas-editor__color-swatch"
                style={{ backgroundColor: selectedColor }}
              />
              <input
                aria-label="Studio text color"
                autoComplete="off"
                disabled={!area.isEditing}
                maxLength={7}
                onBlur={() => {
                  const normalized = normalizeHexColor(colorDraft);
                  setColorDraft(normalized || selectedColor);
                }}
                onChange={(event) => updateSelectedColor(event.target.value)}
                placeholder="#302B27"
                spellCheck={false}
                type="text"
                value={colorDraft}
              />
            </span>
            <NativeSpectrumPicker
              label="Studio text color"
              onChange={updateSelectedColor}
              value={selectedColor}
            />
            <span className="native-canvas-spectrum__presets">
              <button
                disabled={!area.isEditing}
                onClick={() => updateSelectedColor("#8F5C38")}
                type="button"
              >
                Walnut (#8F5C38)
              </button>
              <button
                disabled={!area.isEditing}
                onClick={() => updateSelectedColor("#0A0A0A")}
                type="button"
              >
                Obsidian (#0A0A0A)
              </button>
            </span>
          </div>
          <label className="dynamic-mesh-block__editor-field">
            Text content
            <textarea
              aria-label="Studio block text"
              disabled={!area.isEditing}
              maxLength={2_000}
              onChange={(event) =>
                handlers.updateComponent(
                  selectedComponent.id,
                  "content",
                  event.target.value,
                )
              }
              rows={3}
              value={selectedComponent.content || ""}
            />
          </label>
          {selectedComponent.type === "navigation_link_menu_bar" && (
            <fieldset
              aria-label="Navigation Link Menu Bar settings"
              className="canvas-navigation-menu-editor"
              disabled={!area.isEditing}
            >
              <legend>Navigation link settings</legend>
              {(selectedComponent.menuItems || []).map((item, index) => (
                <div className="canvas-navigation-menu-editor__row" key={item.id}>
                  <label className="dynamic-mesh-block__editor-field">
                    Menu item title
                    <input
                      aria-label={`Canvas menu item ${index + 1} title`}
                      maxLength={80}
                      onChange={(event) =>
                        handlers.updateComponent(
                          selectedComponent.id,
                          "menuItems",
                          (selectedComponent.menuItems || []).map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, label: event.target.value }
                              : row,
                          ),
                        )
                      }
                      value={item.label}
                    />
                  </label>
                  <label className="dynamic-mesh-block__editor-field">
                    Destination page
                    <select
                      aria-label={`Canvas menu item ${index + 1} destination`}
                      onChange={(event) =>
                        handlers.updateComponent(
                          selectedComponent.id,
                          "menuItems",
                          (selectedComponent.menuItems || []).map((row, rowIndex) =>
                            rowIndex === index
                              ? { ...row, targetTabId: event.target.value }
                              : row,
                          ),
                        )
                      }
                      value={item.targetTabId}
                    >
                      {area.subTabs.map((tab) => (
                        <option key={tab.tabId} value={tab.tabId}>
                          {tab.tabLabelDisplayName}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    aria-label={`Remove canvas menu item ${index + 1}`}
                    onClick={() =>
                      handlers.updateComponent(
                        selectedComponent.id,
                        "menuItems",
                        (selectedComponent.menuItems || []).filter(
                          (_, rowIndex) => rowIndex !== index,
                        ),
                      )
                    }
                    type="button"
                  >
                    Remove
                  </button>
                </div>
              ))}
              <button
                className="canvas-navigation-menu-editor__add"
                disabled={
                  (selectedComponent.menuItems || []).length >= 20 ||
                  !area.subTabs.length
                }
                onClick={() => {
                  const destination = area.subTabs[0]?.tabId || area.activeSubTabId;
                  if (!destination) return;
                  handlers.updateComponent(selectedComponent.id, "menuItems", [
                    ...(selectedComponent.menuItems || []),
                    {
                      id: `menu-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
                      label: "New menu item",
                      targetTabId: destination,
                    },
                  ]);
                }}
                type="button"
              >
                ➕ Add Menu Link Item
              </button>
            </fieldset>
          )}
        </div>
      ) : (
        <p className="canvas-studio-editor__empty">
          Select a canvas block to edit its title, color, and content.
        </p>
      )}
    </aside>
  );
}

export function CanvasSubTabArea({
  canManageCanvas,
  isEditing,
  subTabs,
  activeSubTabId,
  onSelectSubTab,
  onAddSubTab,
  activeTabTitle,
  onActiveTabTitleChange,
  layoutMode,
  groups,
  visibleComponents,
  gridColumns,
  rootStyle,
  handlers,
  emptyMessage = "➕ Drop Component Box Here",
  hideLegacyMasterOverviewScaffold = false,
}) {
  const shared = useContext(CanvasSubTabContext) || {};
  const area = {
    ...shared,
    canManageCanvas: shared.canManageCanvas ?? canManageCanvas,
    isEditing: shared.isEditing ?? isEditing,
    subTabs: shared.subTabs ?? subTabs ?? [],
    activeSubTabId: shared.activeSubTabId ?? activeSubTabId,
    onSelectSubTab: shared.onSelectSubTab ?? onSelectSubTab,
    onAddSubTab: shared.onAddSubTab ?? onAddSubTab,
    activeTabTitle: shared.activeTabTitle ?? activeTabTitle,
    onActiveTabTitleChange: shared.onActiveTabTitleChange ?? onActiveTabTitleChange,
    layoutMode: shared.layoutMode ?? layoutMode,
    groups: shared.groups ?? groups ?? [],
    visibleComponents: shared.visibleComponents ?? visibleComponents ?? [],
    gridColumns: shared.gridColumns ?? gridColumns,
    rootStyle: shared.rootStyle ?? rootStyle,
    activeThemeId: shared.activeThemeId ?? "",
    handlers: shared.handlers ?? handlers,
    hideLegacyMasterOverviewScaffold,
  };
  const isCanvasStudio =
    shared.dashboardViewport === CANVAS_STUDIO_VIEWPORT;
  const isLegacyMasterOverviewScaffold =
    area.hideLegacyMasterOverviewScaffold &&
    (area.subTabs.length === 0 ||
      (area.subTabs.length === 1 &&
        area.subTabs[0].tabId === "master-overview" &&
        area.activeSubTabId === "master-overview"));
  const canvasArea = (
    <div
      className={`dynamic-mesh-layout is-${area.layoutMode}`}
      data-canvas-root-layout="true"
      data-theme-id={area.activeThemeId || undefined}
      onDragOver={(event) => {
        if (area.isEditing) event.preventDefault();
      }}
      onDrop={(event) => {
        if (area.isEditing) area.handlers.moveOrPositionComponent(event);
      }}
      style={{
        ...area.rootStyle,
        display: area.layoutMode === "absolute" ? "block" : "grid",
        ...(area.layoutMode === "grid"
          ? {
              gridTemplateColumns: `repeat(${area.gridColumns}, minmax(0, 1fr))`,
              gap: "var(--dynamic-mesh-gap)",
            }
          : {}),
      }}
    >
      {area.groups.map((group) => {
        const placement = group.components[0];
        const groupStyle = group.components.length > 1 && area.layoutMode === "grid"
          ? {
              gridColumn: placement.gridColumn
                ? `${clamp(placement.gridColumn, 1, area.gridColumns, 1)} / span ${clamp(placement.gridColumnSpan, 1, area.gridColumns, 1)}`
                : `span ${clamp(placement.gridColumnSpan, 1, area.gridColumns, 1)}`,
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 180px), 1fr))",
              gap: "var(--dynamic-mesh-gap)",
            }
          : {};
        return (
          <div
            className="dynamic-mesh-group canvas-subtab-page"
            key={`${area.activeSubTabId || "canvas"}-${group.id}`}
            style={groupStyle}
          >
            {group.components.map((component) => {
              const index = area.visibleComponents.indexOf(component);
              return (
                <DynamicMeshBlock
                  canEdit={area.canManageCanvas && area.isEditing}
                  component={component}
                  context={area.handlers.context}
                  gridColumns={area.gridColumns}
                  index={index}
                  activeSubTabId={area.activeSubTabId}
                  isEditing={area.canManageCanvas && area.isEditing}
                  isSelected={area.handlers.selectedComponentId === component.id}
                  selectedComponentId={area.handlers.selectedComponentId}
                  key={component.id || `${component.type}-${index}`}
                  layoutMode={groupStyle.display ? "grid" : area.layoutMode}
                  addToolboxComponent={area.handlers.addToolboxComponent}
                  onDragStart={area.handlers.startComponentDrag}
                  onDragEnd={() => area.handlers.setDraggedComponentId(null)}
                  onDrop={area.handlers.moveOrPositionComponent}
                  onMergePrevious={area.handlers.mergeWithPrevious}
                  onRefresh={area.handlers.loadBlueprint}
                  onRemove={area.handlers.removeComponent}
                  onResize={area.handlers.resizeComponent}
                  onSelect={area.handlers.setSelectedComponentId}
                  onNavigateTab={area.onSelectSubTab}
                  onUngroup={area.handlers.ungroupComponent}
                  onUpdate={area.handlers.updateComponent}
                  onUpdateStyles={area.handlers.updateComponentStyles}
                  onUploadImage={area.handlers.uploadImage}
                  parentId={null}
                  roleKey={area.handlers.roleKey}
                  subTabs={area.subTabs}
                />
              );
            })}
          </div>
        );
      })}
      {area.canManageCanvas &&
        area.visibleComponents.length === 0 &&
        !isLegacyMasterOverviewScaffold && (
        <p className="dynamic-mesh-empty">{emptyMessage}</p>
      )}
    </div>
  );
  return (
    <section
      aria-label="Canvas sub-tabs"
      className={[
        "dynamic-mesh-subtabs",
        isLegacyMasterOverviewScaffold
          ? "dynamic-mesh-subtabs--unframed"
          : "",
      ].filter(Boolean).join(" ")}
      data-canvas-editor-ui="true"
    >
      <div className="dynamic-mesh-subtabs__toolbar" data-canvas-editor-ui="true">
        {area.canManageCanvas && (
          <>
            {!isLegacyMasterOverviewScaffold && (
              <div className="dynamic-mesh-subtabs__tabs" role="tablist" aria-label="Canvas tabs">
                {area.subTabs.map((tab) => (
                  <button
                    aria-selected={tab.tabId === area.activeSubTabId}
                    className={tab.tabId === area.activeSubTabId ? "is-active" : ""}
                    key={tab.tabId}
                    onClick={() => area.onSelectSubTab(tab.tabId)}
                    role="tab"
                    type="button"
                  >
                    {tab.tabLabelDisplayName}
                  </button>
                ))}
              </div>
            )}
            {area.isEditing && (
              <div className="dynamic-mesh-subtabs__add">
                <input
                  aria-label="New sub-canvas tab title"
                  maxLength={80}
                  onChange={(event) => area.onActiveTabTitleChange(event.target.value)}
                  placeholder="Sub-canvas title"
                  value={area.activeTabTitle}
                />
                <button
                  disabled={
                    !String(area.activeTabTitle || "").trim() ||
                    area.subTabs.length >= 16
                  }
                  onClick={area.onAddSubTab}
                  type="button"
                >
                  ➕ Add Sub-Canvas Tab
                </button>
              </div>
            )}
          </>
        )}
      </div>
      {isCanvasStudio ? (
        <div className="canvas-studio-editor-layout">
          {canvasArea}
          <CanvasStudioEditorDrawer area={area} />
        </div>
      ) : canvasArea}
    </section>
  );
}

export function ActiveCanvasMeshProfile({
  children,
  dashboardViewportOverride = null,
  onStandaloneExport = null,
  pageTarget,
  role,
  standaloneExportMessage = "",
  standaloneExportState = "idle",
  viewportRole,
  user = null,
}) {
  const activeRole = role || viewportRole;
  const normalizedPageTarget = pageTarget || PAGE_TARGET_BY_ROLE[activeRole] || "";
  const dashboardViewport =
    dashboardViewportOverride ||
    VIEWPORT_BY_PAGE_TARGET[normalizedPageTarget] ||
    VIEWPORT_BY_ROLE[activeRole];
  const roleKey = ROLE_KEY_BY_PAGE_TARGET[normalizedPageTarget] ||
    ROLE_KEY_BY_PAGE_TARGET[
      Object.keys(VIEWPORT_BY_ROLE).find(
        (role) => VIEWPORT_BY_ROLE[role] === dashboardViewport,
      )
    ] ||
    "scholar";
  const canManageCanvas = user?.role === "MASTER_ADMIN";
  const [blueprint, setBlueprint] = useState(null);
  const [canvasLayout, setCanvasLayout] = useState([]);
  const [subTabs, setSubTabs] = useState([]);
  const [activeSubTabId, setActiveSubTabId] = useState(null);
  const [activeTabTitle, setActiveTabTitle] = useState("");
  const [nativeProfileElements, setNativeProfileElements] = useState([]);
  const [, setLoadState] = useState("loading");
  const [isEditStudioMode, setIsEditStudioMode] = useState(false);
  const isEditing = isEditStudioMode;
  const [selectedComponentId, setSelectedComponentId] = useState(null);
  const [selectedNativeElement, setSelectedNativeElement] = useState(null);
  const [draggedComponentId, setDraggedComponentId] = useState(null);
  const [saveState, setSaveState] = useState("idle");
  const [editorMessage, setEditorMessage] = useState("");
  const positionCommitRequestedRef = useRef(false);
  const positionCommitTimerRef = useRef(null);
  const currentLayoutStateRef = useRef(null);
  currentLayoutStateRef.current = {
    canvasLayout,
    nativeProfileElements,
    subTabs,
    activeSubTabId,
    activeThemeId: blueprint?.layout?.activeThemeId || null,
  };

  function requestStudioDispatch() {
    if (dashboardViewport === CANVAS_STUDIO_VIEWPORT) {
      positionCommitRequestedRef.current = true;
    }
  }

  useEffect(() => {
    setIsEditStudioMode(false);
    setSelectedComponentId(null);
    setSelectedNativeElement(null);
  }, [canManageCanvas, dashboardViewport]);

  const loadBlueprint = useCallback(async () => {
    if (!dashboardViewport) {
      setLoadState("unavailable");
      return null;
    }
    try {
      const query =
        `dashboard_viewport=${encodeURIComponent(dashboardViewport)}` +
        (canManageCanvas ? "&include_hidden=true" : "");
      const response = await fetch(
        `/api/admin/canvas/load-blueprint?${query}`,
        {
          credentials: "include",
          headers: { Accept: "application/json" },
        },
      );
      if (!response.ok) {
        setLoadState("unavailable");
        return null;
      }
      const payload = await response.json();
      setBlueprint(payload);
      const loadedTabs = normalizeSubTabs(payload?.layout);
      setSubTabs(loadedTabs.tabs);
      setActiveSubTabId(loadedTabs.activeId);
      setActiveTabTitle("");
      setCanvasLayout(
        loadedTabs.activeTab.canvasLayout,
      );
      setNativeProfileElements(
        Array.isArray(payload?.layout?.nativeProfileElements)
          ? payload.layout.nativeProfileElements
          : [],
      );
      setLoadState("ready");
      return payload;
    } catch {
      setLoadState("unavailable");
      return null;
    }
  }, [canManageCanvas, dashboardViewport]);

  useEffect(() => {
    function handleLocalBlueprintUpdate(event) {
      const payload = event.detail?.blueprint;
      if (
        event.detail?.dashboardViewport !== dashboardViewport ||
        !payload?.layout
      ) {
        return;
      }
      const loadedTabs = normalizeSubTabs(payload.layout);
      const nextThemeId = payload.layout.activeThemeId || null;
      const themeChanged =
        Boolean(nextThemeId) &&
        nextThemeId !== (currentLayoutStateRef.current.activeThemeId || null);
      const localTabs = new Map(
        currentLayoutStateRef.current.subTabs.map((tab) => [tab.tabId, tab]),
      );
      const mergedTabs = loadedTabs.tabs.map((tab) => {
        const localTab = localTabs.get(tab.tabId);
        const mergedTab = localTab ? { ...tab, ...localTab } : tab;
        const mergedCanvasLayout =
          localTab?.canvasLayout || tab.canvasLayout;
        return {
          ...mergedTab,
          canvasLayout: themeChanged
            ? applyStudioThemeToComponentTree(mergedCanvasLayout, nextThemeId)
            : mergedCanvasLayout,
        };
      });
      const nextActiveId = loadedTabs.activeId;
      const nextActiveTab = mergedTabs.find((tab) => tab.tabId === nextActiveId);
      setBlueprint(payload);
      setSubTabs(mergedTabs);
      setActiveSubTabId(nextActiveId);
      setActiveTabTitle("");
      setCanvasLayout(nextActiveTab?.canvasLayout || []);
      setNativeProfileElements(
        Array.isArray(payload.layout.nativeProfileElements)
          ? payload.layout.nativeProfileElements
          : [],
      );
      setLoadState("ready");
    }
    window.addEventListener(
      "fieldnotes:canvas-blueprint-updated",
      handleLocalBlueprintUpdate,
    );
    return () =>
      window.removeEventListener(
        "fieldnotes:canvas-blueprint-updated",
        handleLocalBlueprintUpdate,
      );
  }, [dashboardViewport]);

  useEffect(() => {
    let active = true;
    setBlueprint(null);
    setCanvasLayout([]);
    setSubTabs([]);
    setActiveSubTabId(null);
    setNativeProfileElements([]);
    setLoadState("loading");
    setSaveState("idle");
    if (!dashboardViewport) {
      setLoadState("unavailable");
      return () => {
        active = false;
      };
    }
    async function loadInitialBlueprint() {
      try {
        const query =
          `dashboard_viewport=${encodeURIComponent(dashboardViewport)}` +
          (canManageCanvas ? "&include_hidden=true" : "");
        const response = await fetch(
          `/api/admin/canvas/load-blueprint?${query}`,
          {
            credentials: "include",
            headers: { Accept: "application/json" },
          },
        );
        if (!response.ok) {
          if (active) setLoadState("unavailable");
          return;
        }
        const payload = await response.json();
        if (active) {
          setBlueprint(payload);
          const loadedTabs = normalizeSubTabs(payload?.layout);
          setSubTabs(loadedTabs.tabs);
          setActiveSubTabId(loadedTabs.activeId);
          setActiveTabTitle("");
          setCanvasLayout(
            loadedTabs.activeTab.canvasLayout,
          );
          setNativeProfileElements(
            Array.isArray(payload?.layout?.nativeProfileElements)
              ? payload.layout.nativeProfileElements
              : [],
          );
          setLoadState("ready");
        }
      } catch {
        if (active) setLoadState("unavailable");
      }
    }
    void loadInitialBlueprint();
    return () => {
      active = false;
    };
  }, [canManageCanvas, dashboardViewport]);

  const applyLiveLayout = useCallback((payload) => {
    setBlueprint(payload);
    const loadedTabs = normalizeSubTabs(payload?.layout);
    setSubTabs(loadedTabs.tabs);
    setActiveSubTabId(loadedTabs.activeId);
    setActiveTabTitle("");
    setCanvasLayout(
      loadedTabs.activeTab.canvasLayout,
    );
    setNativeProfileElements(
      Array.isArray(payload?.layout?.nativeProfileElements)
        ? payload.layout.nativeProfileElements
        : [],
    );
    setSaveState("idle");
    setEditorMessage("A live canvas update was received.");
    setLoadState("ready");
  }, []);
  useEffect(() => {
    if (!activeSubTabId) return;
    setSubTabs((tabs) => tabs.map((tab) =>
      tab.tabId === activeSubTabId ? { ...tab, canvasLayout } : tab
    ));
  }, [activeSubTabId, canvasLayout]);
  useCanvasLayoutStream({
    dashboardViewport,
    onLayout: applyLiveLayout,
  });
  const layout = blueprint?.layout || {};
  const gridColumns = clamp(layout.gridColumns, 1, 12, 12);
  const gridRows = clamp(layout.gridRows, 1, 30, 1);
  const gap = clamp(layout.gap, 0, 40, 32);
  const canvasHeight = clamp(layout.canvasHeight, 240, 1_600, 480);
  const layoutMode = layout.layoutMode === "absolute" ? "absolute" : "grid";
  useEffect(() => {
    if (
      dashboardViewport !== CANVAS_STUDIO_VIEWPORT ||
      !blueprint?.layout ||
      !Array.isArray(blueprint.layout.subTabs) ||
      blueprint.layout.subTabs.length === 0 ||
      !activeSubTabId
    ) {
      return;
    }
    const orderedCanvasLayout = orderCanvasComponents(canvasLayout).map(
      (component, index) => ({
        ...component,
        gridOrderIndex: index,
      }),
    );
    const nextSubTabs = subTabs.map((tab) =>
      tab.tabId === activeSubTabId
        ? {
            ...tab,
            layoutMode,
            gridColumns,
            gridRows,
            gap,
            canvasHeight,
            canvasLayout: orderedCanvasLayout,
          }
        : tab,
    );
    const nextLayout = {
      ...layout,
      schemaVersion: 1,
      layoutMode,
      gridColumns,
      gridRows,
      gap,
      canvasHeight,
      canvasLayout: orderedCanvasLayout,
      nativeProfileElements,
      subTabs: nextSubTabs,
      activeSubTabId,
    };
    window.dispatchEvent(
      new CustomEvent("fieldnotes:canvas-studio-state-updated", {
        detail: {
          dashboardViewport,
          layout: nextLayout,
        },
      }),
    );
  }, [
    activeSubTabId,
    blueprint,
    canvasHeight,
    canvasLayout,
    dashboardViewport,
    gap,
    gridColumns,
    gridRows,
    layout,
    layoutMode,
    nativeProfileElements,
    subTabs,
  ]);
  const visibleComponents = useMemo(
    () =>
      filterCanvasComponentTreeForRole(
        orderCanvasComponents(canvasLayout),
        roleKey,
        canManageCanvas,
      ),
    [canvasLayout, canManageCanvas, roleKey],
  );
  const groups = useMemo(() => blocksWithGroups(visibleComponents), [visibleComponents]);
  const context = {
    blueprint,
    roleName: user?.role || viewportRole || roleKey,
    user,
  };
  const rootStyle = {
    "--dynamic-mesh-gap": `${gap}px`,
    "--dynamic-mesh-columns": gridColumns,
    "--dynamic-mesh-height": `${canvasHeight}px`,
    gridTemplateRows: `repeat(${gridRows}, minmax(90px, auto))`,
    backgroundColor: getStudioTheme(layout.activeThemeId)?.tokens.canvas,
    color: getStudioTheme(layout.activeThemeId)?.tokens.text,
    "--dynamic-mesh-accent": getStudioTheme(layout.activeThemeId)?.tokens.accent,
  };

  function updateComponent(componentId, field, value) {
    if (
      [
        "gridOrderIndex",
        "gridColumn",
        "gridRow",
        "gridColumnSpan",
        "gridRowSpan",
        "x",
        "y",
      ].includes(field)
    ) {
      positionCommitRequestedRef.current = true;
    }
    requestStudioDispatch();
    setCanvasLayout((current) =>
      updateCanvasComponentTree(current, componentId, (component) => ({
        ...component,
        [field]: value,
      })),
    );
    setSaveState("idle");
    setEditorMessage("");
  }

  function updateComponentStyles(componentId, field, value) {
    requestStudioDispatch();
    setCanvasLayout((current) =>
      updateCanvasComponentTree(current, componentId, (component) => ({
        ...component,
        styles: {
          padding: 16,
          margin: 0,
          fontSize: 14,
          fontFamily: "system",
          color: "#302B27",
          backgroundColor: "#FFFFFF",
          borderRadius: 12,
          fontWeight: 400,
          textAlign: "left",
          ...(component.styles || {}),
          [field]: value,
        },
      })),
    );
    setSaveState("idle");
    setEditorMessage("");
  }

  const registerNativeElements = useCallback((elements) => {
    setNativeProfileElements((current) => {
      const existingIds = new Set(current.map((element) => element.id));
      const discoveredById = new Map(elements.map((element) => [element.id, element]));
      const additions = elements
        .filter((element) => !existingIds.has(element.id))
        .map(({ id, label, elementKind, order, gridColumnSpan }) => ({
          id,
          label,
          elementKind,
          order,
          ...(Number.isFinite(gridColumnSpan) ? { gridColumnSpan } : {}),
          ...(elementKind === "container"
            ? { gridOrderIndex: order }
            : { flexOrderWeight: order }),
          visibility: {
            master_admin: true,
            principal: true,
            grade_hod: true,
            educator: true,
            parent: true,
            scholar: true,
          },
        }));
      let changed = additions.length > 0;
      const refreshed = current.map((element) => {
        const discovered = discoveredById.get(element.id);
        if (!discovered) return element;
        const sourceTextChanged = element.label !== discovered.label;
        let refreshedElement = element;
        if (sourceTextChanged) {
          changed = true;
          refreshedElement = { ...refreshedElement, label: discovered.label };
          delete refreshedElement.labelOverride;
          delete refreshedElement.textOverride;
        }
        if (!refreshedElement.elementKind && discovered.elementKind) {
          changed = true;
          refreshedElement = {
            ...refreshedElement,
            elementKind: discovered.elementKind,
          };
        }
        if (
          !Number.isFinite(refreshedElement.gridColumnSpan) &&
          Number.isFinite(discovered.gridColumnSpan)
        ) {
          changed = true;
          refreshedElement = {
            ...refreshedElement,
            gridColumnSpan: discovered.gridColumnSpan,
          };
        }
        return refreshedElement;
      });
      return changed ? [...refreshed, ...additions] : current;
    });
  }, []);

  const updateNativeElement = useCallback((elementId, patch) => {
    if (
      [
        "gridOrderIndex",
        "flexOrderWeight",
        "gridColumnSpan",
        "gridRowSpan",
      ].some((field) => Object.hasOwn(patch, field))
    ) {
      positionCommitRequestedRef.current = true;
    }
    setNativeProfileElements((current) => {
      const index = current.findIndex((element) => element.id === elementId);
      if (index < 0) {
        return [
          ...current,
          {
            id: elementId,
            label: patch.label || "Profile element",
            visibility: { ...DEFAULT_INLINE_VISIBILITY },
            ...patch,
          },
        ];
      }
      return current.map((element, itemIndex) =>
        itemIndex === index
          ? {
              ...element,
              ...patch,
              styles: patch.styles
                ? { ...(element.styles || {}), ...patch.styles }
                : element.styles,
            }
          : element,
      );
    });
    setSaveState("idle");
    setEditorMessage("");
  }, []);

  const selectNativeElement = useCallback((element) => {
    setSelectedNativeElement((current) =>
      current?.id === (element?.id || null) ? current : element,
    );
  }, []);

  const reorderNativeElements = useCallback(
    (orderedIds, orderField = "gridOrderIndex") => {
      setNativeProfileElements((current) => {
        const orderById = new Map(orderedIds.map((id, index) => [id, index]));
        return current.map((element) => {
          if (!orderById.has(element.id)) return element;
          const nextOrder = orderById.get(element.id);
          if (
            orderField === "flexOrderWeight" &&
            element.elementKind !== "container"
          ) {
            return {
              ...element,
              flexOrderWeight: nextOrder,
              order: nextOrder,
            };
          }
          if (
            orderField === "gridOrderIndex" &&
            element.elementKind === "container"
          ) {
            return {
              ...element,
              gridOrderIndex: nextOrder,
              order: nextOrder,
            };
          }
          return element;
        });
      });
      positionCommitRequestedRef.current = true;
      setSaveState("idle");
      setEditorMessage("");
    },
    [],
  );

  const resizeNativeElement = useCallback((elementId, width, height) => {
    updateNativeElement(elementId, {
      styles: {
        widthPx: clamp(Math.round(width), 120, 1_440, 360),
        heightPx: clamp(Math.round(height), 80, 1_600, 180),
      },
    });
  }, [updateNativeElement]);

  function removeComponent(componentId) {
    const component = findCanvasComponentLocation(canvasLayout, componentId)?.component;
    if (!confirmCanvasComponentDeletion(component?.label)) return;
    requestStudioDispatch();
    setCanvasLayout((current) =>
      removeCanvasComponentTree(current, componentId).components,
    );
    setSelectedComponentId(null);
    setSaveState("idle");
    setEditorMessage("");
  }

  function mergeWithPrevious(componentId, parentId = null) {
    const location = findCanvasComponentLocation(canvasLayout, componentId);
    if (!location || location.parentId !== parentId) return;
    const orderedCanvasLayout = orderCanvasComponents(location.siblings);
    const index = orderedCanvasLayout.findIndex(
      (component) => component.id === componentId,
    );
    if (index <= 0) return;
    const current = orderedCanvasLayout[index];
    const previous = orderedCanvasLayout[index - 1];
    requestStudioDispatch();
    const groupId =
      previous.groupId || current.groupId || `inline-group-${previous.id}-${current.id}`;
    const oldGroupIds = new Set([previous.groupId, current.groupId].filter(Boolean));
    const nextSiblings = location.siblings.map((component) =>
        component.id === previous.id ||
        component.id === current.id ||
        oldGroupIds.has(component.groupId)
          ? { ...component, groupId }
          : component,
      );
    setCanvasLayout((items) =>
      parentId
        ? updateCanvasComponentTree(items, parentId, (parent) => ({
            ...parent,
            children: nextSiblings,
          }))
        : nextSiblings,
    );
    setSaveState("idle");
    setEditorMessage("Blocks merged into one canvas group.");
  }

  function ungroupComponent(componentId, parentId = null) {
    const location = findCanvasComponentLocation(canvasLayout, componentId);
    if (!location || location.parentId !== parentId) return;
    const component = location.component;
    if (!component?.groupId) return;
    requestStudioDispatch();
    const groupId = component.groupId;
    const nextSiblings = location.siblings.map((item) =>
        item.groupId === groupId ? { ...item, groupId: null } : item,
      );
    setCanvasLayout((items) =>
      parentId
        ? updateCanvasComponentTree(items, parentId, (parent) => ({
            ...parent,
            children: nextSiblings,
          }))
        : nextSiblings,
    );
    setSaveState("idle");
    setEditorMessage("Canvas group separated.");
  }

  function resizeComponent(
    componentId,
    dimensions,
    parentId = null,
    componentLayoutMode = layoutMode,
  ) {
    const location = findCanvasComponentLocation(canvasLayout, componentId);
    if (!location || location.parentId !== parentId) return;
    requestStudioDispatch();
    if (componentLayoutMode === "absolute") {
      setCanvasLayout((items) =>
        updateCanvasComponentTree(items, componentId, (component) => ({
          ...component,
          width: clamp(dimensions.width, 1, 100, component.width || 50),
          height: clamp(dimensions.height, 1, 100, component.height || 24),
        })),
      );
    } else {
      setCanvasLayout((items) =>
        updateCanvasComponentTree(items, componentId, (component) => ({
          ...component,
          styles: {
            ...(component.styles || {}),
            widthPx: clamp(dimensions.widthPx, 120, 1_440, 360),
            heightPx: clamp(dimensions.heightPx, 80, 1_600, 180),
          },
        })),
      );
    }
    setSaveState("idle");
    setEditorMessage("");
  }

  function uploadImage(componentId, eventOrFile) {
    const input = eventOrFile?.target;
    const file = input?.files?.[0] || eventOrFile;
    if (input) input.value = "";
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      setEditorMessage("Choose a PNG, JPEG, or WebP image.");
      return;
    }
    if (file.size > 180_000) {
      setEditorMessage("Canvas images must be 180 KB or smaller.");
      return;
    }
    requestStudioDispatch();
    const reader = new FileReader();
    reader.onload = () => {
      updateComponent(componentId, "imageSettings", {
        dataUrl: String(reader.result || ""),
        sourceUrl: "",
        alt: findCanvasComponentLocation(canvasLayout, componentId)?.component
          ?.imageSettings?.alt || "Canvas image",
      });
      setEditorMessage("Image added. Save to publish the layout update.");
    };
    reader.onerror = () => {
      setEditorMessage("The selected image could not be read.");
    };
    reader.readAsDataURL(file);
  }

  function startComponentDrag(event, componentId) {
    setDraggedComponentId(componentId);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("application/x-fieldnotes-inline-component", componentId);
  }

  function addToolboxComponent(toolboxItemId, targetId = null, parentId = null) {
    if (!canManageCanvas || !isEditing) return;
    const toolboxItem = PROFILE_COMPONENT_TOOLBOX_ITEMS.find(
      (item) => item.id === toolboxItemId,
    );
    if (!toolboxItem) return;
    if (
      toolboxItem.principalOnly &&
      dashboardViewport !== "Principal Overview Dashboard"
    ) {
      setEditorMessage("This component is available only on the Principal canvas.");
      setSaveState("error");
      return;
    }

    const targetSlot = !parentId && targetId
      ? canvasLayout.find((item) => item.id === targetId)
      : !parentId && dashboardViewport === CANVAS_STUDIO_VIEWPORT
        ? canvasLayout.find((item) =>
            item.groupId?.startsWith(CANVAS_STUDIO_SLOT_PREFIX),
          )
        : null;
    const replacingStudioSlot = Boolean(
      targetSlot?.groupId?.startsWith(CANVAS_STUDIO_SLOT_PREFIX),
    );
    const nodeCount = flattenCanvasComponentTree(canvasLayout).length;
    if (nodeCount >= 80 && !replacingStudioSlot) {
      setEditorMessage("This canvas already has the maximum of 80 blocks.");
      setSaveState("error");
      return;
    }

    const isContainer = toolboxItem.type === MASTER_OVERVIEW_CONTAINER_TYPE;
    const themedStyles = getThemedComponentStyles(
      blueprint?.layout?.activeThemeId,
      toolboxItem.type,
    );
    const component = {
      id: `profile-tool-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      type: toolboxItem.type,
      label: isContainer ? "Master Overview" : toolboxItem.label,
      content: toolboxItem.content || "",
      headingLevel: toolboxItem.headingLevel || null,
      svgMarkup: "",
      dataBinding: "",
      actionTrigger: "",
      functionBinding: "",
      variableBinding: "",
      href: "",
      options: [],
      menuItems: [],
      exportScope: "scholar",
      responseStates: [],
      visibility: isContainer
        ? {
            master_admin: true,
            principal: true,
            grade_hod: false,
            educator: false,
            parent: false,
            scholar: false,
          }
        : {
            master_admin: true,
            principal: true,
            grade_hod: true,
            educator: true,
            parent: true,
            scholar: true,
          },
      inputAnchors: [],
      outputAnchors: [],
      gridColumn: null,
      gridRow: null,
      gridColumnSpan: 6,
      gridRowSpan: 1,
      x: 0,
      y: 0,
      width: 50,
      height: 24,
      styles: {
        padding: 16,
        margin: 0,
        fontSize: 14,
        fontFamily: "system",
        fontWeight: 400,
        textAlign: "left",
        color: "#302B27",
        backgroundColor: "#FFFFFF",
        borderColor: "#D8D0C5",
        borderRadius: 12,
        ...toolboxItem.styles,
        ...(themedStyles || {}),
      },
      imageSettings: null,
      ...(isContainer
        ? {
            children: [],
            childLayoutMode: "grid",
            childGridColumns: 3,
            childGap: 12,
            childCanvasHeight: 360,
          }
        : {}),
    };

    if (!replacingStudioSlot && !canInsertCanvasComponentTree(
      canvasLayout,
      parentId,
      component,
    )) {
      setEditorMessage(
        "Nested containers allow up to 20 direct blocks, 80 total blocks, and 4 levels of nesting.",
      );
      setSaveState("error");
      return;
    }

    if (parentId) {
      setCanvasLayout((items) => {
        const parent = findCanvasComponentLocation(items, parentId)?.component;
        const siblings = parent?.children || [];
        const target = targetId
          ? findCanvasComponentLocation(items, targetId)
          : null;
        const index =
          target?.parentId === parentId ? target.index : siblings.length;
        const added = insertCanvasComponentTree(
          items,
          parentId,
          { ...component, gridOrderIndex: index },
          index,
        );
        return added.inserted ? added.components : items;
      });
    } else if (replacingStudioSlot) {
      setCanvasLayout((items) =>
        orderCanvasComponents(items).map((item, index) =>
          item.id === targetSlot.id
            ? {
                ...component,
                gridColumn: targetSlot.gridColumn,
                gridRow: targetSlot.gridRow,
                gridColumnSpan: targetSlot.gridColumnSpan,
                gridRowSpan: targetSlot.gridRowSpan,
                gridOrderIndex: targetSlot.gridOrderIndex ?? index,
              }
            : { ...item, gridOrderIndex: index },
        ),
      );
    } else {
      setCanvasLayout((items) => {
        const orderedItems = orderCanvasComponents(items).map(
          (item, index) => ({
            ...item,
            gridOrderIndex: index,
          }),
        );
        return [
          ...orderedItems,
          { ...component, gridOrderIndex: orderedItems.length },
        ];
      });
    }
    setSelectedComponentId(component.id);
    requestStudioDispatch();
    setSaveState("idle");
    setEditorMessage(
      dashboardViewport === CANVAS_STUDIO_VIEWPORT
        ? `${toolboxItem.label} added. Saving the studio template…`
        : `${toolboxItem.label} added. Save to publish the layout update.`,
    );
  }

  function moveOrPositionComponent(event, targetId = null, parentId = null) {
    event.preventDefault();
    event.stopPropagation();
    const toolboxItemId =
      event.dataTransfer?.getData(PROFILE_TOOLBOX_DRAG_TYPE) || "";
    if (toolboxItemId) {
      setDraggedComponentId(null);
      addToolboxComponent(toolboxItemId, targetId, parentId);
      return;
    }
    const sourceId =
      event.dataTransfer?.getData("application/x-fieldnotes-inline-component") ||
      draggedComponentId;
    setDraggedComponentId(null);
    if (!sourceId || sourceId === targetId) return;

    const destinationParent = parentId
      ? findCanvasComponentLocation(canvasLayout, parentId)?.component
      : null;
    const destinationLayoutMode =
      destinationParent?.childLayoutMode === "absolute" ? "absolute" : layoutMode;
    let position = null;
    if (destinationLayoutMode === "absolute") {
      const canvasElement =
        event.currentTarget.closest("[data-canvas-nested-layout]") ||
        event.currentTarget.closest("[data-canvas-root-layout]");
      const canvasRect = canvasElement?.getBoundingClientRect();
      if (!canvasRect?.width || !canvasRect?.height) return;
      position = {
        x: clamp(
          ((event.clientX - canvasRect.left) / canvasRect.width) * 100,
          0,
          100,
          0,
        ),
        y: clamp(
          ((event.clientY - canvasRect.top) / canvasRect.height) * 100,
          0,
          100,
          0,
        ),
      };
    }

    const result = moveCanvasComponentTree(canvasLayout, {
      sourceId,
      parentId,
      targetId,
      layoutMode: destinationLayoutMode,
      position,
    });
    if (!result.moved) {
      if (result.reason === "cycle") {
        setEditorMessage("A container cannot be moved inside itself or its descendants.");
      } else if (result.reason === "parent-full") {
        setEditorMessage("A nested canvas can contain at most 20 direct blocks.");
      }
      return;
    }
    const stats = canvasTreeStats(result.components);
    if (stats.nodeCount > 80 || stats.maxDepth > 4) {
      setEditorMessage(
        "Nested containers allow up to 20 direct blocks, 80 total blocks, and 4 levels of nesting.",
      );
      setSaveState("error");
      return;
    }
    requestStudioDispatch();
    setCanvasLayout(result.components);
    positionCommitRequestedRef.current = true;
    setSaveState("idle");
    setEditorMessage("");
  }

  function selectSubTab(tabId) {
    const tab = subTabs.find((item) => item.tabId === tabId);
    if (!tab || tabId === activeSubTabId) return;
    const currentSettings = {
      layoutMode,
      gridColumns,
      gridRows: clamp(layout.gridRows, 1, 30, 1),
      gap: clamp(layout.gap, 0, 40, 32),
      canvasHeight: clamp(layout.canvasHeight, 240, 1_600, 480),
    };
    const selectedSettings = {
      layoutMode: tab.layoutMode || "grid",
      gridColumns: clamp(tab.gridColumns, 1, 12, gridColumns),
      gridRows: clamp(tab.gridRows, 1, 30, currentSettings.gridRows),
      gap: clamp(tab.gap, 0, 40, currentSettings.gap),
      canvasHeight: clamp(
        tab.canvasHeight,
        240,
        1_600,
        currentSettings.canvasHeight,
      ),
    };
    setSubTabs((tabs) => tabs.map((item) =>
      item.tabId === activeSubTabId
        ? { ...item, ...currentSettings, canvasLayout }
        : item
    ));
    setBlueprint((current) => current
      ? {
          ...current,
          layout: {
            ...current.layout,
            ...selectedSettings,
            canvasLayout: tab.canvasLayout,
            activeSubTabId: tabId,
          },
        }
      : current
    );
    setActiveSubTabId(tabId);
    setCanvasLayout(tab.canvasLayout);
    setActiveTabTitle("");
    setSelectedComponentId(null);
  }

  function addSubTab() {
    if (!canManageCanvas || !isEditing) return;
    const label = String(activeTabTitle || "").trim();
    if (!label || subTabs.length >= 16) return;
    const displayOrderWeight = subTabs.reduce(
      (highest, item) => Math.max(highest, item.displayOrderWeight),
      -1,
    ) + 1;
    const tab = {
      tabId: `canvas-tab-${Date.now()}-${subTabs.length + 1}`,
      tabLabelDisplayName: label.slice(0, 80),
      displayOrderWeight,
      status: "active",
      layoutMode,
      gridColumns,
      gridRows: clamp(layout.gridRows, 1, 30, 1),
      gap: clamp(layout.gap, 0, 40, 32),
      canvasHeight: clamp(layout.canvasHeight, 240, 1_600, 480),
      canvasLayout: [],
    };
    setSubTabs((tabs) => [
      ...tabs.map((item) => item.tabId === activeSubTabId ? { ...item, canvasLayout } : item),
      tab,
    ]);
    setActiveSubTabId(tab.tabId);
    setCanvasLayout([]);
    setActiveTabTitle("");
    setSelectedComponentId(null);
    setSaveState("idle");
  }

  async function saveLayoutMutations() {
    if (!canManageCanvas || !isEditing) return;
    const submittedState = currentLayoutStateRef.current;
    if (positionCommitTimerRef.current !== null) {
      clearTimeout(positionCommitTimerRef.current);
      positionCommitTimerRef.current = null;
    }
    positionCommitRequestedRef.current = false;
    if (
      flattenCanvasComponentTree(canvasLayout).some(
        (component) => !String(component.label || "").trim(),
      )
    ) {
      setSaveState("error");
      setEditorMessage("Every canvas block needs a title before saving.");
      return;
    }
    if (
      nativeProfileElements.some((element) =>
        !String(element.label || "").trim() ||
        [
          element.labelOverride,
          element.textOverride,
          element.placeholderOverride,
          element.ariaLabelOverride,
          element.altOverride,
        ].some((value) => value !== null && value !== undefined && !String(value).trim())
      )
    ) {
      setSaveState("error");
      setEditorMessage("Every native profile element needs a title before saving.");
      return;
    }
    if (flattenCanvasComponentTree(canvasLayout).some((component) =>
      component.functionBinding === "LAUNCH_EXTERNAL_HYPERLINK" &&
      !safeInternalOrHttpsUrl(component.href)
    )) {
      setSaveState("error");
      setEditorMessage("Each button link must be a same-site path or HTTPS URL.");
      return;
    }
    const orderedCanvasLayout = orderCanvasComponents(canvasLayout).map(
      (component, index) => ({
        ...component,
        gridOrderIndex: index,
      }),
    );
    const nextLayout = {
      schemaVersion: 1,
      activeThemeId: layout.activeThemeId || null,
      layoutMode,
      gridColumns,
      gridRows: clamp(layout.gridRows, 1, 30, 1),
      gap: clamp(layout.gap, 0, 40, 32),
      canvasHeight: clamp(layout.canvasHeight, 240, 1_600, 480),
      canvasLayout: orderedCanvasLayout,
      nativeProfileElements,
      subTabs: subTabs.map((tab) =>
        tab.tabId === activeSubTabId
          ? {
              ...tab,
              layoutMode,
              gridColumns,
              gridRows: clamp(layout.gridRows, 1, 30, 1),
              gap: clamp(layout.gap, 0, 40, 32),
              canvasHeight: clamp(layout.canvasHeight, 240, 1_600, 480),
              canvasLayout: orderedCanvasLayout,
            }
          : tab
      ),
      activeSubTabId,
    };
    setSaveState("saving");
    setEditorMessage("");
    try {
      const response = await fetch("/api/admin/canvas/save-blueprint", {
        method: "POST",
        credentials: "include",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          editingTargetView: dashboardViewport,
          layout: nextLayout,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string"
            ? payload.detail
            : "The canvas layout could not be saved.",
        );
      }
      setBlueprint(payload);
      const hasNewerLocalEdits =
        currentLayoutStateRef.current.canvasLayout !==
          submittedState.canvasLayout ||
        currentLayoutStateRef.current.nativeProfileElements !==
          submittedState.nativeProfileElements ||
        currentLayoutStateRef.current.subTabs !== submittedState.subTabs ||
        currentLayoutStateRef.current.activeSubTabId !==
          submittedState.activeSubTabId ||
        currentLayoutStateRef.current.activeThemeId !==
          submittedState.activeThemeId;
      if (hasNewerLocalEdits) {
        setSaveState("idle");
        setEditorMessage(
          "This layout version was saved. Newer edits remain in the editor and need another save.",
        );
        return;
      }
      const savedTabs = normalizeSubTabs(payload?.layout || nextLayout);
      setSubTabs(savedTabs.tabs);
      setActiveSubTabId(savedTabs.activeId);
      setActiveTabTitle("");
      setCanvasLayout(
        savedTabs.activeTab.canvasLayout,
      );
      setNativeProfileElements(
        Array.isArray(payload?.layout?.nativeProfileElements)
          ? payload.layout.nativeProfileElements
          : nativeProfileElements,
      );
      setSaveState("saved");
      setEditorMessage("Layout mutations saved and sent to connected profiles.");
    } catch (error) {
      setSaveState("error");
      setEditorMessage(
        error instanceof Error
          ? error.message
          : "The canvas layout could not be saved.",
      );
    }
  }

  useEffect(() => {
    if (
      !positionCommitRequestedRef.current ||
      !canManageCanvas ||
      !isEditing ||
      saveState === "saving"
    ) {
      return undefined;
    }
    if (positionCommitTimerRef.current !== null) {
      clearTimeout(positionCommitTimerRef.current);
    }
    positionCommitTimerRef.current = window.setTimeout(() => {
      positionCommitTimerRef.current = null;
      positionCommitRequestedRef.current = false;
      void saveLayoutMutations();
    }, dashboardViewport === CANVAS_STUDIO_VIEWPORT ? 500 : 250);
    return () => {
      if (positionCommitTimerRef.current !== null) {
        clearTimeout(positionCommitTimerRef.current);
        positionCommitTimerRef.current = null;
      }
    };
  }, [
    activeSubTabId,
    blueprint,
    canManageCanvas,
    canvasLayout,
    dashboardViewport,
    isEditing,
    nativeProfileElements,
    saveState,
    subTabs,
  ]);

  useEffect(() => () => {
    if (positionCommitTimerRef.current !== null) {
      clearTimeout(positionCommitTimerRef.current);
      positionCommitTimerRef.current = null;
    }
  }, []);

  const canvasSubTabContextValue = {
    canManageCanvas,
    isEditing,
    dashboardViewport,
    activeThemeId: layout.activeThemeId || "",
    onStandaloneExport,
    standaloneExportMessage,
    standaloneExportState,
    subTabs,
    activeSubTabId,
    onSelectSubTab: selectSubTab,
    onAddSubTab: addSubTab,
    activeTabTitle,
    onActiveTabTitleChange: setActiveTabTitle,
    layoutMode,
    groups,
    visibleComponents,
    gridColumns,
    rootStyle,
    handlers: {
      context,
      loadBlueprint,
      mergeWithPrevious,
      moveOrPositionComponent,
      removeComponent,
      resizeComponent,
      roleKey,
      selectedComponentId,
      setDraggedComponentId,
      setSelectedComponentId,
      startComponentDrag,
      ungroupComponent,
      updateComponent,
      updateComponentStyles,
      uploadImage,
      addToolboxComponent,
    },
  };
  function toggleStudioMode() {
    if (saveState === "saving") return;
    if (isEditStudioMode && positionCommitRequestedRef.current) {
      void saveLayoutMutations();
    }
    setIsEditStudioMode((current) => !current);
  }

  return (
    <CanvasSubTabContext.Provider value={canvasSubTabContextValue}>
      <div
        className="dynamic-mesh-container"
        data-page-target={normalizedPageTarget}
        style={rootStyle}
      >
      {canManageCanvas && (
        <aside
          aria-label="Canvas layout administration"
          className="dynamic-mesh-admin-banner"
          data-canvas-editor-ui="true"
        >
          <div>
            <strong>Active canvas profile · {dashboardViewport}</strong>
            <span>
              {isEditing
                ? "Position changes publish automatically; other edits publish when saved."
                : "Preview mode. Canvas editing tools are off until studio mode is enabled."}
            </span>
          </div>
          <div className="dynamic-mesh-admin-banner__actions">
            <button
              aria-pressed={isEditStudioMode}
              className="dynamic-mesh-admin-banner__mode"
              disabled={saveState === "saving"}
              onClick={toggleStudioMode}
              type="button"
            >
              {isEditing ? "👁️ Return to Preview" : "🛠️ Enter Studio Edit Mode"}
            </button>
            {isEditing && (
              <button
                aria-label="🚀 Save & Deploy Layout Mutations"
                className="dynamic-mesh-admin-banner__save"
                data-save-state={saveState}
                disabled={saveState === "saving"}
                onClick={saveLayoutMutations}
                type="button"
              >
                🚀 Save &amp; Deploy Layout Mutations
              </button>
            )}
          </div>
          {editorMessage && (
            <p
              aria-live="polite"
              className={`dynamic-mesh-admin-banner__message is-${saveState}`}
              role={saveState === "error" ? "alert" : "status"}
            >
              {editorMessage}
            </p>
          )}
        </aside>
      )}
      <DynamicEditableMeshWrapper
        canEdit={canManageCanvas && isEditing}
        className="dynamic-mesh-native-content"
        onAddToolboxComponent={addToolboxComponent}
        nativeProfileElements={nativeProfileElements}
        onRegisterElements={registerNativeElements}
        onReorderElements={reorderNativeElements}
        onResizeElement={resizeNativeElement}
        onSelectElement={selectNativeElement}
        onUpdateElement={updateNativeElement}
        profileKey={roleKey}
        selectedElementId={selectedNativeElement?.id || null}
      >
        {children}
      </DynamicEditableMeshWrapper>
      </div>
    </CanvasSubTabContext.Provider>
  );
}

export default ActiveCanvasMeshProfile;
export { ActiveCanvasMeshProfile as DynamicMeshContainer };