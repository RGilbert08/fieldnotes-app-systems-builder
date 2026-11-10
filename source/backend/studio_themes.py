"""Shared, bounded theme tokens used by both studio builders and exports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "shared" / "studio-themes.json"
with _CATALOG_PATH.open(encoding="utf-8") as catalog_file:
    _CATALOG = json.load(catalog_file)
_WEBSITE_FONT_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "shared" / "website-fonts.json"
)
with _WEBSITE_FONT_CATALOG_PATH.open(encoding="utf-8") as font_catalog_file:
    _WEBSITE_FONT_CATALOG = json.load(font_catalog_file)

STUDIO_THEMES: tuple[dict[str, Any], ...] = tuple(_CATALOG["themes"])
STUDIO_THEME_BY_ID = {theme["id"]: theme for theme in STUDIO_THEMES}
STUDIO_THEME_IDS = frozenset(STUDIO_THEME_BY_ID)
WEBSITE_FONT_OPTIONS: tuple[dict[str, str], ...] = tuple(
    _WEBSITE_FONT_CATALOG["fonts"]
)
STUDIO_FONT_STACKS = {
    theme["fontKey"]: theme["fontStack"] for theme in STUDIO_THEMES
}
STUDIO_FONT_STACKS.update({
    font["key"]: font["fontStack"] for font in WEBSITE_FONT_OPTIONS
})
STUDIO_FONT_KEYS = frozenset(STUDIO_FONT_STACKS)
LEGACY_FONT_KEYS = frozenset({"system", "serif", "sans-serif", "monospace"})


def is_valid_studio_theme_id(value: str) -> bool:
    return value in STUDIO_THEME_IDS


def is_valid_studio_font_key(value: str) -> bool:
    return value in STUDIO_FONT_KEYS or value in LEGACY_FONT_KEYS


def studio_font_stack(font_key: str) -> str:
    if font_key in STUDIO_FONT_STACKS:
        return STUDIO_FONT_STACKS[font_key]
    return {
        "system": "system-ui, sans-serif",
        "serif": "Georgia, serif",
        "sans-serif": "Arial, sans-serif",
        "monospace": "ui-monospace, monospace",
    }.get(font_key, "system-ui, sans-serif")


def studio_theme(theme_id: str | None) -> dict[str, Any] | None:
    if not theme_id:
        return None
    return STUDIO_THEME_BY_ID.get(theme_id)