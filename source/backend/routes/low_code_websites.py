"""Tenant-scoped storage for validated semantic website drafts."""

from __future__ import annotations

import base64
import json
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from datetime import datetime
from io import BytesIO
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.orm import Session

from backend.authentication.login_gate import get_database_session
from backend.low_code_folders import (
    SEMANTIC_WEBSITES,
    WorkspaceFolderCreatePayload,
    WorkspaceFolderMovePayload,
    create_workspace_folder,
    get_workspace_item_folder,
    list_workspace_folders,
    list_workspace_item_folders,
    move_workspace_item,
    remove_workspace_item_folder,
    workspace_folder_response,
)
from backend.models import (
    LowCodeWebsiteSnapshot,
    PublishedWebsiteRevision,
    WebsiteMediaAsset,
    utc_now,
)
from backend.studio_themes import is_valid_studio_font_key, is_valid_studio_theme_id
from backend.studio_tenant_context import (
    authenticate_studio_master_admin,
    resolve_studio_website_owner,
)

router = APIRouter(
    prefix="/api/studio/websites",
    tags=["semantic low-code websites"],
)

PROJECT_HUB_VIEWPORT = "Global Project Hub"
MAX_WEBSITE_BYTES = 1_500_000
MAX_WEBSITE_ASSET_BYTES = 500_000
MAX_WEBSITE_MEDIA_FILE_BYTES = 180_000
MAX_WEBSITE_MEDIA_PIXELS = 30_000_000
WEBSITE_MEDIA_PREVIEW_SIZE = (320, 180)
WEBSITE_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp"}
WEBSITE_TEMPLATE_TYPES = (
    "landing-saas",
    "content-blog",
    "product-showcase",
)
WEBSITE_ACTION_BINDINGS = (
    "",
    "SCROLL_TO_CONTACT_FORM",
    "OPEN_NEWSLETTER_FORM",
    "OPEN_EXTERNAL_LINK",
)
WEBSITE_COMPONENT_TAGS = {
    "heading2": "h2",
    "heading3": "h3",
    "paragraph": "p",
    "text_box": "div",
    "table": "table",
    "blockquote": "blockquote",
    "hero_banner": "figure",
    "inline_video": "video",
    "svg_graphic": "figure",
    "shape": "figure",
    "cta_button": "button",
    "navigation_link": "a",
    "navigation_link_menu_bar": "nav",
    "newsletter_input": "input",
}
SAFE_ANCHOR = re.compile(r"^#[A-Za-z][A-Za-z0-9_-]{0,95}$")
SAFE_PAGE_ROUTE = re.compile(r"^#/page/[a-z0-9]+(?:-[a-z0-9]+)*$")
SAFE_RASTER_DATA_URL = re.compile(
    r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/=]+$"
)
SVG_NAMESPACE = "http://www.w3.org/2000/svg"
SAFE_SVG_ELEMENTS = {
    "svg",
    "g",
    "rect",
    "circle",
    "ellipse",
    "line",
    "polyline",
    "polygon",
    "path",
    "text",
    "tspan",
    "defs",
    "linearGradient",
    "radialGradient",
    "stop",
}
SAFE_SVG_ATTRIBUTES = {
    "xmlns",
    "viewBox",
    "width",
    "height",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "d",
    "points",
    "fill",
    "stroke",
    "stroke-width",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-opacity",
    "fill-opacity",
    "fill-rule",
    "clip-rule",
    "opacity",
    "transform",
    "id",
    "offset",
    "stop-color",
    "stop-opacity",
    "gradientUnits",
    "gradientTransform",
    "spreadMethod",
    "font-family",
    "font-size",
    "font-weight",
    "text-anchor",
    "dominant-baseline",
    "dx",
    "dy",
}
SAFE_SVG_NUMERIC_ATTRIBUTES = {
    "width",
    "height",
    "x",
    "y",
    "x1",
    "y1",
    "x2",
    "y2",
    "cx",
    "cy",
    "r",
    "rx",
    "ry",
    "stroke-width",
    "stroke-opacity",
    "fill-opacity",
    "opacity",
    "stop-opacity",
    "font-size",
    "dx",
    "dy",
}


def _safe_svg_attribute_value(name: str, value: str, ids: set[str]) -> bool:
    if len(value) > 2_000 or any(ord(character) < 32 for character in value):
        return False
    if name == "xmlns":
        return value == SVG_NAMESPACE
    if name == "id":
        return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", value))
    if name in {"fill", "stroke", "stop-color"}:
        local_reference = re.fullmatch(r"url\(#([A-Za-z][A-Za-z0-9_.-]{0,63})\)", value.strip())
        return (
            local_reference is not None and local_reference.group(1) in ids
        ) or bool(
            re.fullmatch(
                r"(?:none|currentColor|transparent|#[0-9a-fA-F]{3,8}|[A-Za-z]{1,24})",
                value.strip(),
            )
        )
    if name in SAFE_SVG_NUMERIC_ATTRIBUTES:
        return bool(
            re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|px|em|rem)?", value)
        )
    if name == "viewBox":
        return bool(re.fullmatch(r"[+\-\d.eE,\s]+", value))
    if name == "d":
        return bool(re.fullmatch(r"[MmZzLlHhVvCcSsQqTtAa0-9eE.,+\-\s]+", value))
    if name == "points":
        return bool(re.fullmatch(r"[0-9eE.,+\-\s]+", value))
    if name in {"transform", "gradientTransform"}:
        return bool(
            re.fullmatch(
                r"(?:\s*(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([0-9eE.,+\-\s]+\)\s*)+",
                value,
            )
        )
    if name == "offset":
        return bool(re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|px|em|rem)?", value))
    if name == "gradientUnits":
        return value in {"objectBoundingBox", "userSpaceOnUse"}
    if name == "spreadMethod":
        return value in {"pad", "reflect", "repeat"}
    if name == "stroke-linecap":
        return value in {"butt", "round", "square"}
    if name == "stroke-linejoin":
        return value in {"miter", "round", "bevel"}
    if name in {"fill-rule", "clip-rule"}:
        return value in {"nonzero", "evenodd"}
    if name == "font-family":
        return bool(re.fullmatch(r"[A-Za-z0-9 ,_-]{1,80}", value))
    if name == "font-weight":
        return bool(re.fullmatch(r"(?:[1-9]00|normal|bold)", value))
    if name == "text-anchor":
        return value in {"start", "middle", "end"}
    if name == "dominant-baseline":
        return value in {"auto", "middle", "central", "hanging", "text-before-edge"}
    return False


def _validate_safe_svg_markup(value: str) -> str:
    if not value:
        return value
    if (
        value != value.strip()
        or len(value.encode("utf-8")) > 20_000
        or re.search(r"<!\s*(?:doctype|entity)|<\?xml-stylesheet", value, re.IGNORECASE)
    ):
        raise ValueError("SVG code must be under 20 KB and cannot contain document declarations or external stylesheets.")
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError as error:
        raise ValueError("Custom code must be valid SVG markup.") from error
    root_namespace, _, root_name = root.tag.rpartition("}")
    root_namespace = root_namespace.removeprefix("{")
    if root_name != "svg" or root_namespace != SVG_NAMESPACE:
        raise ValueError("Custom code must be an SVG document with the SVG namespace.")

    nodes = list(root.iter())
    if len(nodes) > 120:
        raise ValueError("SVG code is limited to 120 elements.")
    ids: set[str] = set()
    for node in nodes:
        namespace, _, local_name = node.tag.rpartition("}")
        namespace = namespace.removeprefix("{")
        if namespace != SVG_NAMESPACE or local_name not in SAFE_SVG_ELEMENTS:
            raise ValueError(f"The SVG element {local_name!r} is not allowed.")
        if len(node.attrib) > 40:
            raise ValueError("An SVG element has too many attributes.")
        element_depth = 0
        parent = node
        while parent is not root:
            element_depth += 1
            if element_depth > 12:
                raise ValueError("SVG nesting is limited to 12 levels.")
            parent = next(
                (candidate for candidate in nodes if parent in list(candidate)),
                root,
            )
        for name, attribute_value in node.attrib.items():
            if name not in SAFE_SVG_ATTRIBUTES:
                raise ValueError(f"The SVG attribute {name!r} is not allowed.")
            if name == "id":
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", attribute_value) or attribute_value in ids:
                    raise ValueError("SVG IDs must be unique and use letters, numbers, dots, or dashes.")
                ids.add(attribute_value)
        if len(node.text or "") + len(node.tail or "") > 2_000:
            raise ValueError("SVG text is limited to 2,000 characters per element.")

    for node in nodes:
        for name, attribute_value in node.attrib.items():
            if not _safe_svg_attribute_value(name, attribute_value, ids):
                raise ValueError(f"The value for SVG {name!r} is not supported.")
    return value


class WebsiteStyle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: str = Field(default="#302B27", pattern=r"^#[0-9a-fA-F]{6}$")
    customTextColor: bool = False
    backgroundColor: str = Field(
        default="#FFFFFF",
        pattern=r"^(?:#[0-9a-fA-F]{6}|transparent)$",
    )
    customBackgroundColor: bool = False
    backgroundMode: Literal["theme", "color", "transparent", "image"] = "theme"
    backgroundImage: str = Field(default="", max_length=250_000)
    imageOpacity: int = Field(default=100, ge=0, le=100)
    imageFit: Literal["cover", "contain", "fill"] = "cover"
    imagePositionX: int = Field(default=50, ge=0, le=100)
    imagePositionY: int = Field(default=50, ge=0, le=100)
    imageFlipX: bool = False
    imageFlipY: bool = False
    imageRotation: int = Field(default=0, ge=0, le=270, multiple_of=90)
    imageFilter: Literal["none", "grayscale", "sepia", "warm", "cool", "vintage"] = "none"
    imageBrightness: int = Field(default=100, ge=0, le=200)
    imageContrast: int = Field(default=100, ge=0, le=200)
    imageSaturation: int = Field(default=100, ge=0, le=200)
    imageBlur: int = Field(default=0, ge=0, le=12)
    borderColor: str = Field(default="#D8D0C5", pattern=r"^#[0-9a-fA-F]{6}$")
    borderWidth: int | None = Field(default=None, ge=0, le=8)
    borderStyle: Literal["solid", "dashed", "dotted", "double"] = "solid"
    borderRadius: int | None = Field(default=None, ge=0, le=48)
    fontFamily: str = Field(default="system", max_length=40)
    fontSize: int | None = Field(default=None, ge=8, le=72)
    fontWeight: int | None = Field(default=None, ge=300, le=900, multiple_of=100)
    lineHeight: float | None = Field(default=None, ge=1, le=2.2)
    textAlign: Literal["left", "center", "right"] = "left"
    textShadow: Literal["none", "soft", "strong"] | None = None
    boxShadow: Literal["none", "soft", "medium", "strong"] = "none"

    @field_validator("fontFamily")
    @classmethod
    def allow_known_website_font(cls, value: str) -> str:
        if not is_valid_studio_font_key(value):
            raise ValueError("Unsupported website font family.")
        return value

    @field_validator("backgroundImage")
    @classmethod
    def validate_background_image(cls, value: str) -> str:
        if not value:
            return value
        if (
            value != value.strip()
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Background images must use a clean image URL.")
        if SAFE_RASTER_DATA_URL.fullmatch(value):
            return value
        if value.startswith("/") and not value.startswith("//"):
            parsed_path = urlsplit(value)
            if not parsed_path.netloc and parsed_path.path.startswith("/"):
                return value
        try:
            parsed = urlsplit(value)
            safe_https_url = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65_535:
                safe_https_url = False
        except ValueError as error:
            raise ValueError("Background images must use a clean HTTPS or same-site URL.") from error
        if not safe_https_url:
            raise ValueError("Background images must use HTTPS, an embedded raster image, or a same-site asset path.")
        return value


class SemanticWebsiteMenuItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    targetSlug: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )

    @field_validator("label")
    @classmethod
    def normalize_menu_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Menu link titles cannot be blank.")
        return normalized


class WebsiteBreakpointLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int | None = Field(default=None, ge=0, le=10_000)
    y: int | None = Field(default=None, ge=0, le=10_000)
    width: int | None = Field(default=None, ge=1, le=10_000)
    height: int | None = Field(default=None, ge=1, le=10_000)
    visible: bool | None = None


class SemanticWebsiteComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    type: str | None = Field(default=None, min_length=1, max_length=32)
    x: int = Field(default=16, ge=0, le=10_000)
    y: int = Field(default=16, ge=0, le=10_000)
    width: int = Field(default=240, ge=1, le=10_000)
    height: int = Field(default=140, ge=1, le=10_000)
    responsiveLayouts: dict[
        Literal["desktop", "tablet", "mobile"], WebsiteBreakpointLayout
    ] = Field(default_factory=dict, max_length=3)
    data: dict[str, Any] = Field(default_factory=dict)
    componentType: str = Field(min_length=1, max_length=32)
    semanticTag: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=120)
    content: str = Field(default="", max_length=2_000)
    href: str = Field(default="", max_length=2_048)
    mediaUrl: str = Field(default="", max_length=250_000)
    mediaAlt: str = Field(default="", max_length=160)
    imageUrl: str = Field(default="", max_length=250_000)
    imageMode: str = Field(default="", max_length=16)
    imageAlt: str = Field(default="", max_length=160)
    actionBinding: str = Field(default="", max_length=48)
    graphicVariant: str = Field(default="orbit", max_length=24)
    shapeVariant: str = Field(default="rectangle", max_length=24)
    svgMarkup: str = Field(default="", max_length=20_000)
    menuItems: list[SemanticWebsiteMenuItem] = Field(default_factory=list, max_length=20)
    styles: WebsiteStyle = Field(default_factory=WebsiteStyle)

    @field_validator("componentType")
    @classmethod
    def allow_known_component_type(cls, value: str) -> str:
        if value not in WEBSITE_COMPONENT_TAGS:
            raise ValueError("Unsupported website component.")
        return value

    @field_validator("semanticTag")
    @classmethod
    def allow_known_semantic_tag(cls, value: str) -> str:
        if value not in set(WEBSITE_COMPONENT_TAGS.values()):
            raise ValueError("Unsupported semantic HTML element.")
        return value

    @field_validator("actionBinding")
    @classmethod
    def allow_known_action_binding(cls, value: str) -> str:
        if value not in WEBSITE_ACTION_BINDINGS:
            raise ValueError("Unsupported website action binding.")
        return value

    @field_validator("graphicVariant")
    @classmethod
    def allow_known_graphic_variant(cls, value: str) -> str:
        if value not in {"orbit", "wave", "spark"}:
            raise ValueError("Unsupported SVG graphic preset.")
        return value

    @field_validator("shapeVariant")
    @classmethod
    def allow_known_shape_variant(cls, value: str) -> str:
        if value not in {
            "rectangle",
            "circle",
            "ellipse",
            "triangle",
            "diamond",
            "star",
            "arrow",
            "line",
            "custom_svg",
        }:
            raise ValueError("Unsupported shape preset.")
        return value

    @field_validator("svgMarkup")
    @classmethod
    def allow_safe_svg_markup(cls, value: str) -> str:
        return _validate_safe_svg_markup(value)

    @field_validator("imageMode")
    @classmethod
    def allow_known_paragraph_image_mode(cls, value: str) -> str:
        if value not in {"", "background", "lightbox"}:
            raise ValueError("Paragraph images must use a background or lightbox display.")
        return value

    @field_validator("href", "mediaUrl", "imageUrl")
    @classmethod
    def validate_website_url(cls, value: str, info) -> str:
        if not value:
            return value
        if value != value.strip() or "\\" in value or any(ord(char) < 32 for char in value):
            raise ValueError("Website links must be clean URLs.")
        if info.field_name == "href" and (
            SAFE_ANCHOR.fullmatch(value) or SAFE_PAGE_ROUTE.fullmatch(value)
        ):
            return value
        if info.field_name in {"mediaUrl", "imageUrl"} and SAFE_RASTER_DATA_URL.fullmatch(value):
            return value
        if info.field_name in {"mediaUrl", "imageUrl"} and value.startswith("/") and not value.startswith("//"):
            parsed_path = urlsplit(value)
            if not parsed_path.netloc and parsed_path.path.startswith("/"):
                return value
        try:
            parsed = urlsplit(value)
            has_safe_https_host = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65_535:
                has_safe_https_host = False
        except ValueError as error:
            raise ValueError("Website links must use HTTPS, a safe page route, or a same-page anchor.") from error
        if not has_safe_https_host:
            raise ValueError("Website links must use HTTPS, a safe page route, a same-page anchor, or a same-site asset path.")
        return value

    @model_validator(mode="after")
    def match_semantic_element_and_action(self) -> "SemanticWebsiteComponent":
        if self.type is not None and self.type != self.componentType:
            raise ValueError("The layout type must match the website component type.")
        self.type = self.componentType
        if self.x + self.width > 10_000 or self.y + self.height > 10_000:
            raise ValueError("Website component bounds exceed the supported canvas size.")
        layout = {"x": self.x, "y": self.y, "width": self.width, "height": self.height}
        for breakpoint in ("desktop", "tablet", "mobile"):
            override = self.responsiveLayouts.get(breakpoint)
            if override is None:
                continue
            values = override.model_dump(exclude_none=True)
            layout.update(
                {
                    key: value
                    for key, value in values.items()
                    if key in {"x", "y", "width", "height"}
                }
            )
            if (
                layout["x"] + layout["width"] > 10_000
                or layout["y"] + layout["height"] > 10_000
            ):
                raise ValueError("Responsive website component bounds exceed the supported canvas size.")
        if WEBSITE_COMPONENT_TAGS[self.componentType] != self.semanticTag:
            raise ValueError("The component type does not match its semantic HTML element.")
        if self.actionBinding == "OPEN_EXTERNAL_LINK" and not self.href.startswith("https://"):
            raise ValueError("External-link actions require an HTTPS destination.")
        if self.componentType == "inline_video" and not self.mediaUrl:
            raise ValueError("Video components require an HTTPS media URL.")
        if self.componentType != "navigation_link_menu_bar" and self.menuItems:
            raise ValueError("Menu links are only supported on navigation menu bars.")
        if self.componentType != "paragraph" and (
            self.imageUrl or self.imageMode or self.imageAlt
        ):
            raise ValueError("Inline image settings are only supported on paragraph components.")
        if self.componentType not in {"hero_banner", "svg_graphic", "shape"} and self.mediaAlt:
            raise ValueError("Image descriptions are only supported on graphic components.")
        if (
            self.mediaUrl.startswith("data:")
            and self.componentType not in {"hero_banner", "svg_graphic", "shape"}
        ):
            raise ValueError("Embedded raster assets are only supported on graphic components.")
        if self.componentType == "shape" and self.shapeVariant == "custom_svg" and not self.svgMarkup:
            raise ValueError("Custom shapes require safe SVG markup.")
        if self.componentType != "shape" and (
            self.shapeVariant != "rectangle" or self.svgMarkup
        ):
            raise ValueError("Shape settings are only supported on shape components.")
        if self.imageUrl.startswith("data:") and self.componentType != "paragraph":
            raise ValueError("Embedded paragraph images are only supported on paragraph components.")
        return self


class SemanticWebsiteSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    semanticTag: str = Field(min_length=1, max_length=16)
    label: str = Field(min_length=1, max_length=120)
    columns: int = Field(default=1, ge=1, le=3)
    widthPx: int | None = Field(default=None, ge=480, le=1_440)
    heightPx: int | None = Field(default=None, ge=160, le=2_400)
    styles: WebsiteStyle = Field(default_factory=WebsiteStyle)
    children: list[SemanticWebsiteComponent] = Field(default_factory=list, max_length=24)

    @field_validator("semanticTag")
    @classmethod
    def allow_known_section_tag(cls, value: str) -> str:
        if value not in {"header", "nav", "section", "main", "article", "aside", "footer", "form"}:
            raise ValueError("Unsupported semantic section element.")
        return value


class SemanticWebsitePage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z0-9_-]+$")
    title: str = Field(min_length=1, max_length=80)
    slug: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )
    seoTitle: str = Field(default="", max_length=70)
    seoDescription: str = Field(default="", max_length=200)
    socialImageUrl: str = Field(default="", max_length=2_048)
    sections: list[SemanticWebsiteSection] = Field(min_length=1, max_length=20)

    @field_validator("seoTitle", "seoDescription")
    @classmethod
    def normalize_page_metadata_text(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Page metadata must not contain control characters.")
        return normalized

    @field_validator("socialImageUrl")
    @classmethod
    def validate_social_image_url(cls, value: str) -> str:
        if not value:
            return value
        if (
            value != value.strip()
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("Social images must use a clean HTTPS or same-site URL.")
        if value.startswith("/") and not value.startswith("//"):
            parsed_path = urlsplit(value)
            if not parsed_path.netloc and parsed_path.path.startswith("/"):
                return value
        try:
            parsed = urlsplit(value)
            safe_https_url = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
            )
            if parsed.port is not None and not 1 <= parsed.port <= 65_535:
                safe_https_url = False
        except ValueError as error:
            raise ValueError(
                "Social images must use a clean HTTPS or same-site URL."
            ) from error
        if not safe_https_url:
            raise ValueError(
                "Social images must use HTTPS or a same-site asset path."
            )
        return value


class SemanticWebsiteBlueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schemaVersion: int = Field(default=1, ge=1, le=1)
    templateType: str = Field(min_length=1, max_length=48)
    sections: list[SemanticWebsiteSection] = Field(min_length=1, max_length=20)
    pages: list[SemanticWebsitePage] = Field(default_factory=list, max_length=20)
    activePageId: str | None = Field(default=None, max_length=96)
    themeId: str | None = Field(default=None, max_length=64)

    @field_validator("templateType")
    @classmethod
    def allow_known_template_type(cls, value: str) -> str:
        if value not in WEBSITE_TEMPLATE_TYPES:
            raise ValueError("Unsupported website template.")
        return value

    @field_validator("themeId")
    @classmethod
    def allow_known_website_theme(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_studio_theme_id(value):
            raise ValueError("Unsupported website theme.")
        return value

    @model_validator(mode="after")
    def validate_unique_ids_and_bounds(self) -> "SemanticWebsiteBlueprint":
        if self.pages:
            page_ids = [page.id for page in self.pages]
            slugs = [page.slug for page in self.pages]
            if len(page_ids) != len(set(page_ids)):
                raise ValueError("Website page IDs must be unique.")
            if len(slugs) != len(set(slugs)):
                raise ValueError("Website page routes must be unique.")
            if self.activePageId is None:
                self.activePageId = self.pages[0].id
            active_page = next(
                (page for page in self.pages if page.id == self.activePageId),
                None,
            )
            if active_page is None:
                raise ValueError("activePageId must reference an existing page.")
            self.sections = active_page.sections
            all_sections = [section for page in self.pages for section in page.sections]
            valid_slugs = set(slugs)
            for section in all_sections:
                for component in section.children:
                    if any(item.targetSlug not in valid_slugs for item in component.menuItems):
                        raise ValueError("Menu links must target an existing website page.")
        else:
            if self.activePageId is not None:
                raise ValueError("activePageId requires a website pages collection.")
            all_sections = self.sections

        section_ids = [section.id for section in all_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("Website section IDs must be unique.")
        component_ids = [
            component.id
            for section in all_sections
            for component in section.children
        ]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("Website component IDs must be unique.")
        if len(component_ids) > 100:
            raise ValueError("A website can contain at most 100 components.")
        embedded_asset_urls = {
            asset_url
            for section in all_sections
            for component in section.children
            for asset_url in (component.mediaUrl, component.imageUrl)
            if asset_url.startswith("data:image/")
        }
        embedded_asset_bytes = sum(
            len(asset_url.encode("utf-8")) for asset_url in embedded_asset_urls
        )
        if embedded_asset_bytes > MAX_WEBSITE_ASSET_BYTES:
            raise ValueError("Embedded website graphics exceed the 500 KB total limit.")
        return self


class WebsiteSavePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    siteId: str | None = Field(default=None, min_length=1, max_length=36)
    siteTitle: str = Field(min_length=1, max_length=120)
    selectedWebTemplateType: str = Field(min_length=1, max_length=48)
    semanticBlueprintData: SemanticWebsiteBlueprint

    @field_validator("siteTitle")
    @classmethod
    def normalize_site_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Website title cannot be blank.")
        return normalized

    @field_validator("selectedWebTemplateType")
    @classmethod
    def allow_known_selected_template(cls, value: str) -> str:
        if value not in WEBSITE_TEMPLATE_TYPES:
            raise ValueError("Unsupported website template.")
        return value

    @model_validator(mode="after")
    def match_blueprint_template(self) -> "WebsiteSavePayload":
        if self.semanticBlueprintData.templateType != self.selectedWebTemplateType:
            raise ValueError("The selected template does not match the website blueprint.")
        return self


def _authorized_tenant(request: Request, session: Session) -> int | None:
    return resolve_studio_website_owner(request, session)


def _website_media_owner(
    request: Request,
    session: Session,
) -> tuple[int | None, int]:
    actor = authenticate_studio_master_admin(request, session)
    tenant_id = resolve_studio_website_owner(
        request,
        session,
        authenticated_user=actor,
    )
    return tenant_id, actor.id


def _website_media_response(
    asset: WebsiteMediaAsset,
    *,
    include_data: bool = False,
    include_preview: bool = False,
) -> dict[str, object]:
    response: dict[str, object] = {
        "assetId": asset.asset_id,
        "publicUrl": f"/api/public/websites/media/{asset.asset_id}",
        "fileName": asset.file_name,
        "altText": asset.alt_text,
        "mimeType": asset.mime_type,
        "createdAt": asset.created_at.isoformat(),
        "ownerScope": "platform" if asset.owner_tenant_id is None else "tenant",
    }
    if include_data:
        response["dataUrl"] = (
            f"data:{asset.mime_type};base64,{asset.image_data}"
        )
    if include_preview:
        response["previewDataUrl"] = _website_media_preview_data_url(asset)
    return response


def _website_media_preview_data_url(asset: WebsiteMediaAsset) -> str | None:
    try:
        image_bytes = base64.b64decode(asset.image_data, validate=True)
        with Image.open(BytesIO(image_bytes)) as image:
            if image.width * image.height > MAX_WEBSITE_MEDIA_PIXELS:
                return None
            image = ImageOps.exif_transpose(image)
            image.thumbnail(WEBSITE_MEDIA_PREVIEW_SIZE, Image.Resampling.LANCZOS)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                preview = background
            else:
                preview = image.convert("RGB")
            output = BytesIO()
            preview.save(output, format="JPEG", quality=55, optimize=True)
    except (
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        UnidentifiedImageError,
        ValueError,
    ):
        return None
    encoded_preview = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded_preview}"


def _website_media_is_referenced_as_social_image(
    session: Session,
    asset_id: str,
) -> bool:
    public_url = f"/api/public/websites/media/{asset_id}"
    snapshots = session.scalars(select(LowCodeWebsiteSnapshot)).all()
    revisions = session.scalars(
        select(PublishedWebsiteRevision).where(
            PublishedWebsiteRevision.hostname.is_not(None)
        )
    ).all()
    return any(
        page.get("socialImageUrl") == public_url
        for record in [*snapshots, *revisions]
        for page in (record.semantic_blueprint_data or {}).get("pages", [])
        if isinstance(page, dict)
    )


class WebsiteMediaUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fileName: str = Field(min_length=1, max_length=255)
    altText: str = Field(default="", max_length=160)

    @field_validator("fileName", "altText")
    @classmethod
    def normalize_media_text(cls, value: str, info) -> str:
        normalized = unicodedata.normalize("NFC", value).strip()
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("Image details must not contain control characters.")
        if info.field_name == "fileName" and not normalized:
            raise ValueError("Image names must contain visible text.")
        return normalized


@router.get("/media")
def list_website_media(
    request: Request,
    response: Response,
    q: str = Query(default="", max_length=160),
    cursor_created_at: datetime | None = None,
    cursor_asset_id: str | None = Query(default=None, min_length=1, max_length=36),
    session: Session = Depends(get_database_session),
):
    if (cursor_created_at is None) != (cursor_asset_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Both media cursor values must be provided together.",
        )
    tenant_id, _actor_id = _website_media_owner(request, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    search = q.strip()
    query = select(WebsiteMediaAsset).where(
        WebsiteMediaAsset.owner_tenant_id == tenant_id
    )
    if search:
        escaped_search = (
            search.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        query = query.where(
            WebsiteMediaAsset.file_name.ilike(f"%{escaped_search}%")
            | WebsiteMediaAsset.alt_text.ilike(f"%{escaped_search}%")
        )
    if cursor_created_at is not None and cursor_asset_id is not None:
        query = query.where(
            or_(
                WebsiteMediaAsset.created_at < cursor_created_at,
                and_(
                    WebsiteMediaAsset.created_at == cursor_created_at,
                    WebsiteMediaAsset.asset_id > cursor_asset_id,
                ),
            )
        )
    assets = session.scalars(
        query.order_by(
            WebsiteMediaAsset.created_at.desc(),
            WebsiteMediaAsset.asset_id,
        ).limit(51)
    ).all()
    has_more = len(assets) > 50
    response.headers["X-Has-More"] = "true" if has_more else "false"
    assets = assets[:50]
    return [
        _website_media_response(asset, include_preview=True)
        for asset in assets
    ]


@router.post("/media", status_code=status.HTTP_201_CREATED)
async def upload_website_media(
    request: Request,
    file: UploadFile = File(...),
    file_name: str = Form(default=""),
    alt_text: str = Form(default=""),
    session: Session = Depends(get_database_session),
):
    tenant_id, actor_id = _website_media_owner(request, session)
    mime_type = (file.content_type or "").lower()
    if mime_type not in WEBSITE_MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Choose a PNG, JPEG, or WebP image.",
        )
    image = await file.read(MAX_WEBSITE_MEDIA_FILE_BYTES + 1)
    await file.close()
    if not image or len(image) > MAX_WEBSITE_MEDIA_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Website images must be 180 KB or smaller.",
        )
    is_valid_image = (
        mime_type == "image/png" and image.startswith(b"\x89PNG\r\n\x1a\n")
    ) or (
        mime_type == "image/jpeg" and image.startswith(b"\xff\xd8\xff")
    ) or (
        mime_type == "image/webp"
        and image.startswith(b"RIFF")
        and image[8:12] == b"WEBP"
    )
    if not is_valid_image:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="The image contents do not match the selected image type.",
        )
    supplied_name = unicodedata.normalize("NFC", file_name.strip())
    original_name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    asset_name = supplied_name or unicodedata.normalize("NFC", original_name)
    asset_name = asset_name[:255]
    if (
        not asset_name
        or asset_name in {".", ".."}
        or any(ord(character) < 32 for character in asset_name)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Enter a valid image name.",
        )
    normalized_alt = unicodedata.normalize("NFC", alt_text.strip())
    if len(normalized_alt) > 160 or any(
        ord(character) < 32 for character in normalized_alt
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Image descriptions must be 160 characters or fewer.",
        )
    asset = WebsiteMediaAsset(
        owner_tenant_id=tenant_id,
        file_name=asset_name,
        alt_text=normalized_alt,
        mime_type=mime_type,
        image_data=base64.b64encode(image).decode("ascii"),
        uploaded_by_user_id=actor_id,
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(asset)
    session.flush()
    result = _website_media_response(asset, include_data=True)
    session.commit()
    return result


@router.put("/media/{asset_id}")
def update_website_media(
    asset_id: str,
    payload: WebsiteMediaUpdatePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    tenant_id, _actor_id = _website_media_owner(request, session)
    asset = session.scalar(
        select(WebsiteMediaAsset)
        .where(
            WebsiteMediaAsset.asset_id == asset_id,
            WebsiteMediaAsset.owner_tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This image is not available to the selected website owner.",
        )
    asset.file_name = payload.fileName
    asset.alt_text = payload.altText
    asset.updated_at = utc_now()
    session.flush()
    result = _website_media_response(asset)
    session.commit()
    return result


@router.get("/media/{asset_id}/content")
def get_website_media_content(
    asset_id: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    tenant_id, _actor_id = _website_media_owner(request, session)
    asset = session.scalar(
        select(WebsiteMediaAsset).where(
            WebsiteMediaAsset.asset_id == asset_id,
            WebsiteMediaAsset.owner_tenant_id == tenant_id,
        )
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This image is not available to the selected website owner.",
        )
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return {"dataUrl": f"data:{asset.mime_type};base64,{asset.image_data}"}


@router.delete("/media/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_website_media(
    asset_id: str,
    request: Request,
    session: Session = Depends(get_database_session),
) -> None:
    tenant_id, _actor_id = _website_media_owner(request, session)
    asset = session.scalar(
        select(WebsiteMediaAsset)
        .where(
            WebsiteMediaAsset.asset_id == asset_id,
            WebsiteMediaAsset.owner_tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This image is not available to the selected website owner.",
        )
    if _website_media_is_referenced_as_social_image(session, asset_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Remove this image from saved and published page settings before deleting it.",
        )
    session.delete(asset)
    session.flush()


def _website_response(
    site: LowCodeWebsiteSnapshot,
    folder: dict[str, object] | None = None,
) -> dict[str, object]:
    folder = folder or {"folderId": None, "folderName": None}
    return {
        "siteId": site.site_id,
        "siteTitle": site.site_title,
        "selectedWebTemplateType": site.selected_web_template_type,
        "semanticBlueprintData": site.semantic_blueprint_data,
        "createdTimestamp": site.created_timestamp.isoformat(),
        "folderId": folder["folderId"],
        "folderName": folder["folderName"],
    }


def _persist_website_draft(
    payload: WebsiteSavePayload,
    tenant_id: int | None,
    session: Session,
) -> tuple[LowCodeWebsiteSnapshot, str]:
    try:
        semantic_data = payload.semanticBlueprintData.model_dump(mode="json")
        if len(json.dumps(semantic_data, separators=(",", ":")).encode()) > MAX_WEBSITE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="The website blueprint exceeds the 1.5 MB size limit.",
            )
    except (TypeError, ValueError) as error:
        if isinstance(error, HTTPException):
            raise
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The website blueprint must contain valid JSON data.",
        ) from error

    if payload.siteId:
        site = session.scalar(
            select(LowCodeWebsiteSnapshot)
            .where(
                LowCodeWebsiteSnapshot.site_id == payload.siteId,
                LowCodeWebsiteSnapshot.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if site is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The website was not found in the active tenant.",
            )
        site.site_title = payload.siteTitle
        site.selected_web_template_type = payload.selectedWebTemplateType
        site.semantic_blueprint_data = semantic_data
        action = "updated"
    else:
        site = LowCodeWebsiteSnapshot(
            site_title=payload.siteTitle,
            selected_web_template_type=payload.selectedWebTemplateType,
            semantic_blueprint_data=semantic_data,
            created_timestamp=utc_now(),
            tenant_id=tenant_id,
        )
        session.add(site)
        action = "created"

    session.commit()
    return site, action


async def _broadcast_website_snapshot_updated(
    request: Request,
    tenant_id: int | None,
    site_id: str | None,
    action: str,
) -> None:
    if tenant_id is None:
        return
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None:
        message: dict[str, object] = {
            "type": "website.snapshots.updated",
            "action": action,
            "refreshTenantWebsiteCache": True,
        }
        if site_id:
            message["siteId"] = site_id
        await hub.broadcast(tenant_id, PROJECT_HUB_VIEWPORT, message)


@router.post("/save")
async def save_website(
    payload: WebsiteSavePayload,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    site, action = _persist_website_draft(payload, tenant_id, session)
    if action == "created":
        response.status_code = status.HTTP_201_CREATED
    result = _website_response(
        site,
        get_workspace_item_folder(
            session,
            tenant_id,
            SEMANTIC_WEBSITES,
            site.site_id,
        ) if tenant_id is not None else None,
    )
    await _broadcast_website_snapshot_updated(
        request,
        tenant_id,
        site.site_id,
        action,
    )
    return result


@router.get("/folders")
def list_website_folders(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    if tenant_id is None:
        return []
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return list_workspace_folders(session, tenant_id, SEMANTIC_WEBSITES)


@router.post("/folders", status_code=status.HTTP_201_CREATED)
async def create_website_folder(
    payload: WorkspaceFolderCreatePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Platform-owned websites do not use tenant folders.",
        )
    folder = create_workspace_folder(
        session,
        tenant_id,
        SEMANTIC_WEBSITES,
        payload.folderName,
    )
    session.commit()
    await _broadcast_website_snapshot_updated(
        request,
        tenant_id,
        None,
        "folder_created",
    )
    return workspace_folder_response(folder)


@router.put("/folder/{site_id}")
async def move_website_to_folder(
    site_id: str,
    payload: WorkspaceFolderMovePayload,
    request: Request,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Platform-owned websites do not use tenant folders.",
        )
    site = session.scalar(
        select(LowCodeWebsiteSnapshot)
        .where(
            LowCodeWebsiteSnapshot.site_id == site_id,
            LowCodeWebsiteSnapshot.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if site is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The website was not found in the active tenant.",
        )
    folder_assignment = move_workspace_item(
        session,
        tenant_id,
        SEMANTIC_WEBSITES,
        site.site_id,
        payload.folderId,
    )
    session.commit()
    await _broadcast_website_snapshot_updated(
        request,
        tenant_id,
        site.site_id,
        "moved",
    )
    return {"siteId": site.site_id, **folder_assignment}


@router.get("/list")
def list_websites(
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    sites = session.scalars(
        select(LowCodeWebsiteSnapshot)
        .where(LowCodeWebsiteSnapshot.tenant_id == tenant_id)
        .order_by(
            LowCodeWebsiteSnapshot.created_timestamp.desc(),
            LowCodeWebsiteSnapshot.site_id,
        )
    ).all()
    folders_by_site = list_workspace_item_folders(
        session,
        tenant_id,
        SEMANTIC_WEBSITES,
        [site.site_id for site in sites],
    ) if tenant_id is not None else {}
    return [
        _website_response(site, folders_by_site.get(site.site_id))
        for site in sites
    ]


@router.get("/read/{site_id}")
def read_website(
    site_id: str,
    request: Request,
    response: Response,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    response.headers["Cache-Control"] = "no-store"
    site = session.scalar(
        select(LowCodeWebsiteSnapshot).where(
            LowCodeWebsiteSnapshot.site_id == site_id,
            LowCodeWebsiteSnapshot.tenant_id == tenant_id,
        )
    )
    if site is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The website was not found in the active tenant.",
        )
    return _website_response(
        site,
        get_workspace_item_folder(
            session,
            tenant_id,
            SEMANTIC_WEBSITES,
            site.site_id,
        ) if tenant_id is not None else None,
    )


@router.delete("/delete/{site_id}")
async def delete_website(
    site_id: str,
    request: Request,
    session: Session = Depends(get_database_session),
):
    tenant_id = _authorized_tenant(request, session)
    site = session.scalar(
        select(LowCodeWebsiteSnapshot)
        .where(
            LowCodeWebsiteSnapshot.site_id == site_id,
            LowCodeWebsiteSnapshot.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if site is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="The website was not found in the active tenant.",
        )
    deleted_site_id = site.site_id
    deleted_revisions = session.execute(
        delete(PublishedWebsiteRevision).where(
            PublishedWebsiteRevision.tenant_id == tenant_id,
            PublishedWebsiteRevision.site_id == deleted_site_id,
        )
    ).rowcount or 0
    if tenant_id is not None:
        remove_workspace_item_folder(
            session,
            tenant_id,
            SEMANTIC_WEBSITES,
            deleted_site_id,
        )
    session.delete(site)
    session.commit()
    hub = getattr(request.app.state, "canvas_layout_stream_hub", None)
    if hub is not None and tenant_id is not None:
        await hub.broadcast(
            tenant_id,
            PROJECT_HUB_VIEWPORT,
            {
                "type": "website.snapshots.updated",
                "action": "deleted",
                "siteId": deleted_site_id,
                "publishedRevisionsDeleted": deleted_revisions,
                "refreshTenantWebsiteCache": True,
            },
        )
    return {
        "siteId": deleted_site_id,
        "deleted": True,
        "publishedRevisionsDeleted": deleted_revisions,
    }