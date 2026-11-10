"""Validated layout contracts shared by the canvas editor and role dashboards."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Literal
import re
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from backend.studio_themes import (
    is_valid_studio_font_key,
    is_valid_studio_theme_id,
)

CANVAS_VIEWPORT_AUDIENCES = {
    "Master Control Room Dashboard": "master_admin",
    "Principal Overview Dashboard": "principal",
    "Grade HOD Analytics Console": "grade_hod",
    "Educator Workspace View": "educator",
    "Parent Insights Dashboard": "parent",
    "Scholar Simulation Portal": "scholar",
    "Canvas Project Studio": "master_admin",
    "Global Project Hub": "master_admin",
}
CANVAS_VIEWPORTS = frozenset(CANVAS_VIEWPORT_AUDIENCES)
CANVAS_PAGE_TARGETS = {
    "MASTER_CONTROL": "Master Control Room Dashboard",
    "PRINCIPAL_COCKPIT": "Principal Overview Dashboard",
    "HOD_ANALYTICS": "Grade HOD Analytics Console",
    "TEACHER_DASHBOARD": "Educator Workspace View",
    "PARENT_PORTAL": "Parent Insights Dashboard",
    "SCHOLAR_PLAYGROUND": "Scholar Simulation Portal",
}
CANVAS_COMPONENT_TYPES = frozenset(
    {
        "metrics_progress_monitor",
        "subject_assessment_data_grid",
        "psychometric_input_field",
        "text_observation_block",
        "rich_text_block",
        "trigger_action_button",
        "custom_button_element",
        "image_asset",
        "secure_link_anchor",
        "video_embed_block",
        "svg_graphic_block",
        "navigation_link_menu_bar",
        "source_document_uploader_module",
        "dynamic_assessment_selector",
        "conditional_logic_shield_wrapper",
        "reactive_calendar_broadcast_dispatcher",
        "curriculum_velocity_pacing_indicator",
        "hod_macro_forecasting_graph_grid",
        "ELibraryDataNode",
        "master_overview_container",
    }
)
CANVAS_ACTION_BINDINGS = frozenset(
    {
        "REFRESH_LAYOUT",
        "EXPORT_ROSTER_DATA",
        "LAUNCH_EXTERNAL_HYPERLINK",
        "ACKNOWLEDGE_TELEMETRY_ALERT",
        "UNLOCK_SIMULATION_CANVAS",
        "TRIGGER_OTP_GATE",
        "ROLLOVER_PAST_PACING",
    }
)
LEGACY_ACTION_TRIGGERS = frozenset(
    {
        "",
        "refresh_data_stream",
        "acknowledge_telemetry_alert",
        "unlock_simulation_canvas",
    }
)
CANVAS_VARIABLE_BINDINGS = frozenset(
    {
        "",
        "CURRENT_PROFILE_NAME",
        "CURRENT_PROFILE_ROLE",
        "ACTIVE_TENANT_ID",
        "LAYOUT_UPDATED_AT",
    }
)
CANVAS_VISIBILITY_ROLES = frozenset(CANVAS_VIEWPORT_AUDIENCES.values())

SAFE_SVG_TAGS = frozenset(
    {"svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "title", "desc"}
)
SAFE_SVG_ATTRIBUTES = frozenset(
    {
        "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2",
        "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "stroke",
        "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
        "stroke-dashoffset", "fill-rule", "opacity", "fill-opacity",
        "stroke-opacity", "id", "role", "aria-label",
    }
)
SAFE_SVG_COLOR = re.compile(
    r"^(?:none|currentColor|transparent|#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?|[A-Za-z]{1,20})$"
)
SAFE_SVG_NUMBER = re.compile(r"^[0-9]+(?:\.[0-9]+)?(?:px|%)?$")
SAFE_SVG_PATH = re.compile(r"^[MmZzLlHhVvCcSsQqTtAaEe0-9,\s.+-]+$")


def _safe_svg_attribute(name: str, value: str) -> bool:
    if (
        name not in SAFE_SVG_ATTRIBUTES
        or len(value) > 8_000
        or "url(" in value.lower()
        or "javascript:" in value.lower()
    ):
        return False
    if name in {"fill", "stroke"}:
        return bool(SAFE_SVG_COLOR.fullmatch(value))
    if name in {"d", "points", "stroke-dasharray"}:
        return bool(SAFE_SVG_PATH.fullmatch(value))
    if name == "viewBox":
        parts = value.replace(",", " ").split()
        return len(parts) == 4 and all(SAFE_SVG_NUMBER.fullmatch(part) for part in parts)
    if name in {"stroke-linecap"}:
        return value in {"butt", "round", "square"}
    if name in {"stroke-linejoin"}:
        return value in {"miter", "round", "bevel"}
    if name in {"id"}:
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value))
    if name == "role":
        return value in {"img", "presentation"}
    if name == "aria-label":
        return bool(re.fullmatch(r"[A-Za-z0-9 _.,!?():-]{1,160}", value))
    return bool(SAFE_SVG_NUMBER.fullmatch(value))


def _safe_svg_markup(markup: str) -> bool:
    if (
        len(markup) > 12_000
        or "<!doctype" in markup.lower()
        or "<!entity" in markup.lower()
        or "<?xml-stylesheet" in markup.lower()
    ):
        return False
    try:
        root = ElementTree.fromstring(markup)
    except ElementTree.ParseError:
        return False
    namespace = root.tag.split("}", 1)[0].lstrip("{") if "}" in root.tag else ""
    root_local_tag = root.tag.rsplit("}", 1)[-1]
    if (
        namespace not in {"", "http://www.w3.org/2000/svg"}
        or root_local_tag != "svg"
    ):
        return False

    pending = [(root, 0)]
    node_count = 0
    while pending:
        node, depth = pending.pop()
        node_count += 1
        local_tag = node.tag.rsplit("}", 1)[-1]
        node_namespace = node.tag.split("}", 1)[0].lstrip("{") if "}" in node.tag else ""
        if (
            node_count > 160
            or depth > 12
            or local_tag not in SAFE_SVG_TAGS
            or node_namespace not in {"", "http://www.w3.org/2000/svg"}
        ):
            return False
        if any(not _safe_svg_attribute(name, value) for name, value in node.attrib.items()):
            return False
        if local_tag not in {"title", "desc"} and (node.text or "").strip():
            return False
        if len(node.text or "") > 240 and local_tag in {"title", "desc"}:
            return False
        if any((child.tail or "").strip() for child in node):
            return False
        pending.extend((child, depth + 1) for child in node)
    return True


def _supported_video_href(value: str) -> bool:
    if not value:
        return True
    try:
        parsed = urlsplit(value)
        if value.startswith("/") and not value.startswith("//"):
            return parsed.path.lower().endswith(".mp4")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        if parsed.path.lower().endswith(".mp4"):
            _ = parsed.port
            return True
        host = parsed.hostname.lower()
        if host not in {
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "www.youtu.be",
        }:
            return False
        if host.endswith("youtu.be"):
            video_id = parsed.path.strip("/").split("/", 1)[0]
        elif parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            video_id = parts[1] if len(parts) == 2 and parts[0] in {"embed", "shorts", "live"} else ""
        return bool(re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id))
    except ValueError:
        return False


class CanvasImageSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataUrl: str = Field(default="", max_length=360_000)
    sourceUrl: str = Field(default="", max_length=2_048)
    alt: str = Field(default="", max_length=240)

    @field_validator("dataUrl")
    @classmethod
    def allow_safe_raster_image_data(cls, value: str) -> str:
        if value and not value.startswith(
            (
                "data:image/png;base64,",
                "data:image/jpeg;base64,",
                "data:image/webp;base64,",
            )
        ):
            raise ValueError("Canvas images must be PNG, JPEG, or WebP data URLs.")
        return value

    @field_validator("sourceUrl")
    @classmethod
    def allow_safe_image_source_url(cls, value: str) -> str:
        if not value:
            return value
        if (
            value != value.strip()
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Image source URLs must be clean same-site paths or HTTPS URLs.")
        try:
            parsed = urlsplit(value)
            safe_same_site_path = (
                value.startswith("/")
                and not value.startswith("//")
                and not parsed.netloc
                and parsed.path.startswith("/")
            )
            safe_https_url = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65_535:
                safe_https_url = False
            if safe_same_site_path or safe_https_url:
                return value
        except ValueError:
            pass
        raise ValueError("Image source URLs must be same-site paths or HTTPS URLs.")


class CanvasBlockStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    padding: int = Field(default=16, ge=0, le=48)
    margin: int = Field(default=0, ge=0, le=32)
    fontSize: int = Field(default=14, ge=8, le=48)
    fontFamily: str = Field(default="system", max_length=40)
    fontWeight: int = Field(default=400, ge=300, le=900, multiple_of=100)
    textAlign: Literal["left", "center", "right"] = "left"
    widthPx: int | None = Field(default=None, ge=120, le=1_440)
    heightPx: int | None = Field(default=None, ge=80, le=1_600)
    color: str = Field(default="#302B27", pattern=r"^#[0-9a-fA-F]{6}$")
    backgroundColor: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    borderColor: str = Field(default="#D8D0C5", pattern=r"^#[0-9a-fA-F]{6}$")
    borderRadius: int = Field(default=12, ge=0, le=32)

    @field_validator("fontFamily")
    @classmethod
    def allow_known_studio_font(cls, value: str) -> str:
        if not is_valid_studio_font_key(value):
            raise ValueError("Unsupported canvas font family.")
        return value


class CanvasMenuItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    targetTabId: str = Field(min_length=1, max_length=96)

    @field_validator("label")
    @classmethod
    def normalize_menu_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Menu link titles cannot be blank.")
        return normalized


class MasterControlCanvasComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    type: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=120)
    dataBinding: str = Field(default="", max_length=96)
    variableBinding: str = Field(default="", max_length=40)
    functionBinding: str = Field(default="", max_length=48)
    actionTrigger: str = Field(default="", max_length=48)
    responseStates: list[str] = Field(default_factory=list, max_length=4)
    visibility: dict[str, bool] = Field(default_factory=dict)
    content: str = Field(default="", max_length=2_000)
    href: str = Field(default="", max_length=2_048)
    headingLevel: Literal[1, 2, 3] | None = None
    svgMarkup: str = Field(default="", max_length=12_000)
    options: list[str] = Field(default_factory=list, max_length=20)
    menuItems: list[CanvasMenuItem] = Field(default_factory=list, max_length=20)
    exportScope: Literal["scholar", "personnel", "combined"] = "scholar"
    nodeDefinition: str | None = Field(default=None, max_length=80)
    inputAnchors: list[str] = Field(default_factory=list, max_length=12)
    outputAnchors: list[str] = Field(default_factory=list, max_length=12)
    tenantFilterBinding: Literal["", "ACTIVE_TENANT_ID"] = ""
    metadataStreamBinding: Literal["", "ELIBRARY_ASSET_METADATA_V1"] = ""
    groupId: str | None = Field(default=None, max_length=160)
    gridColumn: int | None = Field(default=None, ge=1, le=12)
    gridRow: int | None = Field(default=None, ge=1, le=30)
    gridOrderIndex: int | None = Field(default=None, ge=-1_000, le=1_000)
    gridColumnSpan: int = Field(default=1, ge=1, le=12)
    gridRowSpan: int = Field(default=1, ge=1, le=30)
    x: int = Field(default=0, ge=0, le=100)
    y: int = Field(default=0, ge=0, le=100)
    width: int = Field(default=50, ge=1, le=100)
    height: int = Field(default=24, ge=1, le=100)
    styles: CanvasBlockStyle = Field(default_factory=CanvasBlockStyle)
    imageSettings: CanvasImageSettings | None = None
    children: list["MasterControlCanvasComponent"] = Field(
        default_factory=list,
        max_length=20,
    )
    childLayoutMode: Literal["grid", "absolute"] = "grid"
    childGridColumns: int = Field(default=3, ge=1, le=12)
    childGap: int = Field(default=12, ge=0, le=40)
    childCanvasHeight: int = Field(default=360, ge=240, le=1_600)

    @model_validator(mode="after")
    def validate_menu_item_owner(self) -> "MasterControlCanvasComponent":
        if self.type != "navigation_link_menu_bar" and self.menuItems:
            raise ValueError("Menu links are only supported on navigation menu bars.")
        return self

    @field_validator("type")
    @classmethod
    def allow_known_component_types(cls, value: str) -> str:
        if value not in CANVAS_COMPONENT_TYPES:
            raise ValueError("Unsupported canvas component type.")
        return value

    @field_validator("variableBinding")
    @classmethod
    def allow_known_variable_bindings(cls, value: str) -> str:
        if value not in CANVAS_VARIABLE_BINDINGS:
            raise ValueError("Unsupported canvas variable binding.")
        return value

    @field_validator("functionBinding")
    @classmethod
    def allow_known_function_bindings(cls, value: str) -> str:
        if value and value not in CANVAS_ACTION_BINDINGS:
            raise ValueError("Unsupported canvas function binding.")
        return value

    @field_validator("actionTrigger")
    @classmethod
    def allow_known_legacy_action_triggers(cls, value: str) -> str:
        if value not in LEGACY_ACTION_TRIGGERS:
            raise ValueError("Unsupported canvas action trigger.")
        return value

    @field_validator("visibility")
    @classmethod
    def allow_known_visibility_roles(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) - CANVAS_VISIBILITY_ROLES:
            raise ValueError("Unsupported role visibility key.")
        return value

    @field_validator("responseStates")
    @classmethod
    def allow_known_response_states(cls, value: list[str]) -> list[str]:
        allowed = {
            "RSVP_ATTENDING_COUNT",
            "DIGITAL_CONSENT_SIGNATURE",
            "DOCUMENT_UPLOAD_REQUEST",
            "READ_ACKNOWLEDGE_TICK",
        }
        if set(value) - allowed:
            raise ValueError("Unsupported canvas response state.")
        return value

    @field_validator("href")
    @classmethod
    def allow_safe_canvas_links(cls, value: str) -> str:
        if not value:
            return value
        if (
            value != value.strip()
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Canvas links must not contain whitespace controls or backslashes.")
        try:
            parsed = urlsplit(value)
            safe_same_site_path = (
                value.startswith("/")
                and not value.startswith("//")
                and not parsed.netloc
                and parsed.path.startswith("/")
                and not parsed.path.startswith("//")
            )
            safe_https_url = (
                parsed.scheme == "https"
                and parsed.hostname is not None
                and parsed.username is None
                and parsed.password is None
            )
            if safe_same_site_path or safe_https_url:
                _ = parsed.port
                return value
        except ValueError:
            pass
        raise ValueError("Canvas links must be same-site paths or HTTPS URLs.")

    @field_validator("svgMarkup")
    @classmethod
    def allow_safe_svg_graphics(cls, value: str) -> str:
        if value and not _safe_svg_markup(value):
            raise ValueError("SVG graphics may contain only safe, static vector shapes.")
        return value

    @model_validator(mode="after")
    def validate_specialized_component_fields(self) -> "MasterControlCanvasComponent":
        if self.headingLevel is not None and self.type != "rich_text_block":
            raise ValueError("Heading levels are only supported on rich text blocks.")
        if self.svgMarkup and self.type != "svg_graphic_block":
            raise ValueError("SVG markup is only supported on SVG graphic blocks.")
        if (
            (self.tenantFilterBinding or self.metadataStreamBinding)
            and self.nodeDefinition != "ELibraryDataNode"
        ):
            raise ValueError(
                "E-Library data bindings are supported only on E-Library data nodes."
            )
        if self.type == "video_embed_block" and not _supported_video_href(self.href):
            raise ValueError("Videos must use an HTTPS MP4 URL or a supported YouTube URL.")
        has_nested_configuration = (
            bool(self.children)
            or self.childLayoutMode != "grid"
            or self.childGridColumns != 3
            or self.childGap != 12
            or self.childCanvasHeight != 360
        )
        if self.type != "master_overview_container" and has_nested_configuration:
            raise ValueError(
                "Nested canvas children are supported only on the Master Overview container."
            )
        return self

    @field_validator("inputAnchors", "outputAnchors", "options")
    @classmethod
    def trim_bounded_text_lists(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 120 for value in cleaned):
            raise ValueError("Canvas text values must contain 1 to 120 characters.")
        return cleaned


class MasterControlSubTab(BaseModel):
    """A bounded secondary canvas persisted inside the master layout JSON."""

    model_config = ConfigDict(extra="forbid")

    tabId: str = Field(min_length=1, max_length=96)
    tabLabelDisplayName: str = Field(min_length=1, max_length=80)
    displayOrderWeight: int = Field(ge=0, le=1_000)
    status: Literal["active"] = "active"
    layoutMode: Literal["grid", "absolute"] | None = None
    gridColumns: int | None = Field(default=None, ge=1, le=12)
    gridRows: int | None = Field(default=None, ge=1, le=30)
    gap: int | None = Field(default=None, ge=0, le=40)
    canvasHeight: int | None = Field(default=None, ge=240, le=1_600)
    canvasLayout: list[MasterControlCanvasComponent] = Field(
        default_factory=list,
        max_length=80,
    )

    @field_validator("tabId")
    @classmethod
    def trim_tab_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Sub-tab IDs cannot be blank.")
        if normalized != value:
            raise ValueError("Sub-tab IDs cannot contain surrounding whitespace.")
        return normalized

    @field_validator("tabLabelDisplayName")
    @classmethod
    def trim_tab_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Sub-tab labels cannot be blank.")
        return normalized


class NativeProfileCanvasElement(BaseModel):
    """Bounded content and style overrides for an existing profile element."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=160)
    label: str = Field(min_length=1, max_length=120)
    elementKind: Literal["container", "text", "form_control", "button", "media"] | None = None
    labelOverride: str | None = Field(default=None, min_length=1, max_length=120)
    textOverride: str | None = Field(default=None, min_length=1, max_length=1_000)
    placeholderOverride: str | None = Field(default=None, min_length=1, max_length=240)
    ariaLabelOverride: str | None = Field(default=None, min_length=1, max_length=120)
    altOverride: str | None = Field(default=None, min_length=1, max_length=240)
    visibility: dict[str, bool] = Field(default_factory=dict)
    styles: CanvasBlockStyle = Field(default_factory=CanvasBlockStyle)
    gridColumnSpan: int | None = Field(default=None, ge=1, le=12)
    gridRowSpan: int | None = Field(default=None, ge=1, le=30)
    order: int | None = Field(default=None, ge=-1_000, le=1_000)
    gridOrderIndex: int | None = Field(default=None, ge=-1_000, le=1_000)
    flexOrderWeight: int | None = Field(default=None, ge=-1_000, le=1_000)

    @field_validator(
        "labelOverride",
        "textOverride",
        "placeholderOverride",
        "ariaLabelOverride",
        "altOverride",
    )
    @classmethod
    def reject_blank_content_overrides(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Canvas content overrides cannot be blank.")
        return value

    @field_validator("visibility")
    @classmethod
    def allow_known_visibility_roles(cls, value: dict[str, bool]) -> dict[str, bool]:
        if set(value) - CANVAS_VISIBILITY_ROLES:
            raise ValueError("Unsupported role visibility key.")
        return value


def _validate_component_tree(
    components: list[MasterControlCanvasComponent],
) -> None:
    seen_ids: set[str] = set()
    count = 0

    def visit(component: MasterControlCanvasComponent, depth: int) -> None:
        nonlocal count
        count += 1
        if count > 80:
            raise ValueError("Each canvas tree is limited to 80 components.")
        if depth > 4:
            raise ValueError("Canvas containers may be nested no more than 4 levels.")
        if component.id in seen_ids:
            raise ValueError("Component IDs must be unique within each canvas tree.")
        seen_ids.add(component.id)
        for child in component.children:
            visit(child, depth + 1)

    for component in components:
        visit(component, 1)


def validate_canvas_layout_for_viewport(
    layout: "MasterControlLayoutConfiguration | dict[str, object]",
    dashboard_viewport: str,
) -> "MasterControlLayoutConfiguration":
    """Apply viewport-specific component policy after generic bounded parsing."""
    configuration = (
        layout
        if isinstance(layout, MasterControlLayoutConfiguration)
        else MasterControlLayoutConfiguration.model_validate(layout)
    )
    if dashboard_viewport != "Principal Overview Dashboard":
        component_trees = [configuration.canvasLayout]
        component_trees.extend(tab.canvasLayout for tab in configuration.subTabs)
        if any(
            component.type == "master_overview_container"
            for tree in component_trees
            for root in tree
            for component in _walk_canvas_component_tree(root)
        ):
            raise ValueError(
                "The Master Overview container is available only on the Principal canvas."
            )
    return configuration


def _walk_canvas_component_tree(
    component: MasterControlCanvasComponent,
):
    yield component
    for child in component.children:
        yield from _walk_canvas_component_tree(child)


def _preflight_component_tree(components: object) -> None:
    if not isinstance(components, list):
        return
    seen_ids: set[str] = set()
    count = 0

    def visit(component: object, depth: int) -> None:
        nonlocal count
        if not isinstance(component, dict):
            return
        count += 1
        if count > 80:
            raise ValueError("Each canvas tree is limited to 80 components.")
        if depth > 4:
            raise ValueError("Canvas containers may be nested no more than 4 levels.")
        component_id = component.get("id")
        if isinstance(component_id, str):
            if component_id in seen_ids:
                raise ValueError("Component IDs must be unique within each canvas tree.")
            seen_ids.add(component_id)
        children = component.get("children")
        if isinstance(children, list):
            for child in children:
                visit(child, depth + 1)

    for root in components:
        visit(root, 1)


class MasterControlLayoutConfiguration(BaseModel):
    """The versioned, bounded JSON layout stored in a tenant viewport row."""

    model_config = ConfigDict(extra="forbid")

    schemaVersion: Literal[1] = 1
    activeThemeId: str | None = Field(default=None, max_length=64)
    layoutMode: Literal["grid", "absolute"] = "grid"
    gridColumns: int = Field(default=3, ge=1, le=12)
    gridRows: int = Field(default=1, ge=1, le=30)
    gap: int = Field(default=12, ge=0, le=40)
    canvasHeight: int = Field(default=480, ge=240, le=1_600)
    canvasLayout: list[MasterControlCanvasComponent] = Field(
        default_factory=list,
        max_length=80,
    )
    subTabs: list[MasterControlSubTab] = Field(default_factory=list, max_length=16)
    activeSubTabId: str | None = Field(default=None, max_length=96)
    nativeProfileElements: list[NativeProfileCanvasElement] = Field(
        default_factory=list,
        max_length=500,
    )

    @field_validator("activeThemeId")
    @classmethod
    def allow_known_studio_theme(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_studio_theme_id(value):
            raise ValueError("Unsupported canvas theme.")
        return value

    @model_validator(mode="before")
    @classmethod
    def preflight_component_tree_depth(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        _preflight_component_tree(value.get("canvasLayout"))
        tabs = value.get("subTabs")
        if isinstance(tabs, list):
            for tab in tabs:
                if isinstance(tab, dict):
                    _preflight_component_tree(tab.get("canvasLayout"))
        return value

    @model_validator(mode="after")
    def bound_embedded_images(self) -> "MasterControlLayoutConfiguration":
        component_trees = [self.canvasLayout]
        component_trees.extend(tab.canvasLayout for tab in self.subTabs)
        total_image_bytes = sum(
            len(component.imageSettings.dataUrl)
            for tree in component_trees
            for root in tree
            for component in _walk_canvas_component_tree(root)
            if component.imageSettings
        )
        if total_image_bytes > 500_000:
            raise ValueError("Canvas image data exceeds the 500 KB layout limit.")
        return self

    @model_validator(mode="after")
    def bound_component_trees(self) -> "MasterControlLayoutConfiguration":
        _validate_component_tree(self.canvasLayout)
        for tab in self.subTabs:
            _validate_component_tree(tab.canvasLayout)
        return self

    @model_validator(mode="after")
    def validate_sub_tabs(self) -> "MasterControlLayoutConfiguration":
        tab_ids = [tab.tabId for tab in self.subTabs]
        if len(tab_ids) != len(set(tab_ids)):
            raise ValueError("Sub-tab IDs must be unique.")
        weights = [tab.displayOrderWeight for tab in self.subTabs]
        if len(weights) != len(set(weights)):
            raise ValueError("Sub-tab display order weights must be unique.")
        if self.activeSubTabId is not None:
            if not self.subTabs or self.activeSubTabId not in set(tab_ids):
                raise ValueError("activeSubTabId must reference an existing sub-tab.")
        pending = [
            component
            for tab in self.subTabs
            for component in tab.canvasLayout
        ]
        pending.extend(self.canvasLayout)
        known_tab_ids = set(tab_ids)
        while pending:
            component = pending.pop()
            pending.extend(component.children)
            if component.type == "navigation_link_menu_bar":
                if any(item.targetTabId not in known_tab_ids for item in component.menuItems):
                    raise ValueError(
                        "Menu links must target an existing canvas sub-tab."
                    )
        return self


def iter_canvas_components(
    configuration: MasterControlLayoutConfiguration,
) -> Iterator[MasterControlCanvasComponent]:
    """Yield every saved component, including nested containers and sub-tabs."""
    trees = [configuration.canvasLayout]
    trees.extend(tab.canvasLayout for tab in configuration.subTabs)
    for tree in trees:
        for root in tree:
            yield from _walk_canvas_component_tree(root)


def viewport_for_page_target(page_target: str) -> str | None:
    return CANVAS_PAGE_TARGETS.get(page_target)


def viewport_audience(dashboard_viewport: str) -> str | None:
    return CANVAS_VIEWPORT_AUDIENCES.get(dashboard_viewport)


def role_key_for_user_role(role: object) -> str | None:
    value = getattr(role, "value", role)
    return {
        "MASTER_ADMIN": "master_admin",
        "PRINCIPAL": "principal",
        "GRADE_HOD": "grade_hod",
        "EDUCATOR": "educator",
        "PARENT": "parent",
        "SCHOLAR": "scholar",
    }.get(str(value))


def filter_layout_for_role(
    layout: dict[str, object],
    role_key: str,
) -> dict[str, object]:
    """Validate then filter the opaque persisted JSON before it reaches a role UI."""
    configuration = MasterControlLayoutConfiguration.model_validate(layout)
    normalized = configuration.model_dump(
        mode="json",
        by_alias=True,
        exclude_unset=True,
    )
    normalized.setdefault("canvasLayout", [])

    def visible_components(
        components: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        visible = []
        for component in components:
            if not component.get("visibility", {}).get(role_key, True):
                continue
            if (
                role_key not in {"principal", "master_admin"}
                and component.get("functionBinding") == "EXPORT_ROSTER_DATA"
            ):
                component["functionBinding"] = ""
            children = component.get("children")
            if isinstance(children, list):
                component["children"] = visible_components(children)
            visible.append(component)
        return visible

    normalized["canvasLayout"] = visible_components(normalized["canvasLayout"])
    if "subTabs" in normalized:
        for tab in normalized["subTabs"]:
            tab["canvasLayout"] = visible_components(tab["canvasLayout"])
    if "nativeProfileElements" in normalized:
        normalized["nativeProfileElements"] = [
            element
            for element in normalized["nativeProfileElements"]
            if element.get("visibility", {}).get(role_key, True)
        ]
    return normalized