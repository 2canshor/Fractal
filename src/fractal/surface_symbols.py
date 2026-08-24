"""Canonical SF Symbol mappings and checked-in Skill icon verification."""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import Any


class SurfaceSymbolError(RuntimeError):
    """Raised when a user-facing symbol or icon asset drifts from its manifest."""


@lru_cache(maxsize=1)
def load_surface_symbol_manifest() -> dict[str, Any]:
    """Load and validate the deterministic manifest generated from canonical policy."""
    manifest = json.loads(
        files("fractal.data").joinpath("user-surface-symbols.json").read_text(encoding="utf-8")
    )
    if (
        manifest.get("record_type") != "user-surface-symbol-manifest"
        or manifest.get("record_version") != 1
        or manifest.get("symbol_system") != "sf-symbols"
    ):
        raise SurfaceSymbolError("User-surface symbol manifest identity is invalid")
    if manifest.get("sf_symbols_app_version") != "7.0":
        raise SurfaceSymbolError("User-surface symbol manifest requires SF Symbols 7.0")
    palettes = manifest.get("palettes")
    if palettes != {
        "action": {"foreground_color": "#FFFFFF", "outer_color": "#0A84FF"},
        "command": {"foreground_color": "#FFFFFF", "outer_color": "#BF5AF2"},
        "command-outline": {
            "foreground_color": "#BF5AF2",
            "outer_color": "#BF5AF2",
        },
    }:
        raise SurfaceSymbolError("User-surface symbol palettes are invalid")

    symbols = manifest.get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise SurfaceSymbolError("User-surface symbol manifest requires entries")
    entry_ids = [item.get("entry_id") for item in symbols]
    names = [item.get("name") for item in symbols]
    if entry_ids != sorted(entry_ids) or len(entry_ids) != len(set(entry_ids)):
        raise SurfaceSymbolError("User-surface symbol entries must be unique and sorted")
    if len(names) != len(set(names)):
        raise SurfaceSymbolError("Every user-facing Skill must use a distinct SF Symbol")
    action_count = sum(item.get("interface_type") == "action" for item in symbols)
    command_count = sum(item.get("interface_type") == "command" for item in symbols)
    if manifest.get("summary") != {
        "entry_count": len(symbols),
        "action_count": action_count,
        "command_count": command_count,
    }:
        raise SurfaceSymbolError("User-surface symbol summary is invalid")
    if manifest.get("verification_contract") != {
        "required_sizes_px": [16, 20, 24, 32],
        "required_appearances": ["light", "dark"],
        "codex_discovery_order": ["plugin/installed", "skills/list:forceReload"],
        "live_ui_required_after_install": True,
    }:
        raise SurfaceSymbolError("User-surface symbol verification contract is invalid")

    for item in symbols:
        interface_type = item.get("interface_type")
        expected_shape = {"action": "circle", "command": "square"}.get(interface_type)
        if expected_shape is None or item.get("container_shape") != expected_shape:
            raise SurfaceSymbolError(
                f"SF Symbol container does not match interface type: {item.get('entry_id')}"
            )
        expected_palette = interface_type
        if interface_type == "command" and not str(item.get("name")).endswith(".fill"):
            expected_palette = "command-outline"
        if item.get("rendering") != "palette" or item.get("palette") != expected_palette:
            raise SurfaceSymbolError(f"SF Symbol palette is invalid: {item.get('entry_id')}")
        expected_colors = palettes[expected_palette]
        if (
            item.get("outer_color") != expected_colors["outer_color"]
            or item.get("foreground_color") != expected_colors["foreground_color"]
        ):
            raise SurfaceSymbolError(f"SF Symbol colours are invalid: {item.get('entry_id')}")
        if not isinstance(item.get("sf_symbols_introduced"), str):
            raise SurfaceSymbolError(
                f"SF Symbol availability metadata is missing: {item.get('entry_id')}"
            )
        selection = item.get("selection")
        if not isinstance(selection, dict) or set(selection) != {
            "rationale",
            "search_terms",
            "alternatives_considered",
        }:
            raise SurfaceSymbolError(f"SF Symbol selection is incomplete: {item.get('entry_id')}")
        rationale = selection["rationale"]
        search_terms = selection["search_terms"]
        alternatives = selection["alternatives_considered"]
        search_terms_valid = (
            isinstance(search_terms, list)
            and len(search_terms) >= 2
            and all(isinstance(term, str) and len(term) >= 2 for term in search_terms)
        )
        alternatives_valid = (
            isinstance(alternatives, list)
            and bool(alternatives)
            and all(isinstance(name, str) and name for name in alternatives)
        )
        if (
            not isinstance(rationale, str)
            or len(rationale) < 40
            or not search_terms_valid
            or len(search_terms) != len(set(search_terms))
            or not alternatives_valid
            or len(alternatives) != len(set(alternatives))
            or item.get("name") in alternatives
        ):
            raise SurfaceSymbolError(
                f"SF Symbol selection evidence is invalid: {item.get('entry_id')}"
            )
        assets = item.get("assets")
        if not isinstance(assets, dict) or set(assets) != {"small", "large"}:
            raise SurfaceSymbolError(f"SF Symbol assets are incomplete: {item.get('entry_id')}")
        for size_name, pixels in (("small", 400), ("large", 800)):
            asset = assets[size_name]
            if (
                not isinstance(asset, dict)
                or asset.get("pixels") != pixels
                or re.fullmatch(r"[0-9a-f]{64}", str(asset.get("sha256"))) is None
                or not str(asset.get("path", "")).endswith(
                    f"/{item['entry_id']}-{size_name}.png"
                )
                or asset.get("openai_path") != f"./assets/{item['entry_id']}-{size_name}.png"
            ):
                raise SurfaceSymbolError(
                    f"SF Symbol {size_name} asset metadata is invalid: {item.get('entry_id')}"
                )
    return manifest


def surface_symbol_by_entry() -> dict[str, dict[str, Any]]:
    """Return the immutable generated symbol record for each visible entry."""
    return {
        item["entry_id"]: item for item in load_surface_symbol_manifest()["symbols"]
    }


def validate_surface_symbol_entries(entries: list[dict[str, Any]]) -> None:
    """Require each visible entry to match its generated symbol and container class."""
    manifest = surface_symbol_by_entry()
    names = [entry["symbol"]["name"] for entry in entries]
    if len(names) != len(set(names)):
        raise SurfaceSymbolError("Every visible entry must use a distinct SF Symbol")
    for entry in entries:
        entry_id = entry["entry_id"]
        registered = manifest.get(entry_id)
        if registered is None:
            raise SurfaceSymbolError(f"User entry has no registered SF Symbol: {entry_id}")
        symbol = entry["symbol"]
        if symbol != {
            "system": "sf-symbols",
            "name": registered["name"],
            "selection": registered["selection"],
        }:
            raise SurfaceSymbolError(f"User entry SF Symbol drifted: {entry_id}")
        if entry["interface_type"] != registered["interface_type"]:
            raise SurfaceSymbolError(f"User entry symbol class drifted: {entry_id}")
        expected_shape = "circle" if entry["interface_type"] == "action" else "square"
        if registered["container_shape"] != expected_shape:
            raise SurfaceSymbolError(f"User entry symbol container drifted: {entry_id}")


def _metadata_icon_paths(metadata: str) -> dict[str, str]:
    paths: dict[str, str] = {}
    for key in ("icon_small", "icon_large"):
        matches = re.findall(
            rf'^\s{{2}}{key}:\s*["\'](?P<path>[^"\']+)["\']\s*$',
            metadata,
            flags=re.MULTILINE,
        )
        if len(matches) != 1:
            raise SurfaceSymbolError(f"Skill UI metadata requires exactly one {key}")
        paths[key] = matches[0]
    return paths


def _png_properties(data: bytes) -> tuple[int, int, int, int]:
    if len(data) < 26 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise SurfaceSymbolError("Skill icon is not a valid PNG")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    bit_depth = data[24]
    color_type = data[25]
    return width, height, bit_depth, color_type


def validate_skill_symbol_assets(source: Path, metadata: str) -> dict[str, Any] | None:
    """Verify one visible Skill's UI paths, PNGs, dimensions, alpha, and digests."""
    source = Path(source)
    registered = surface_symbol_by_entry().get(source.name)
    if registered is None:
        return None
    icon_paths = _metadata_icon_paths(metadata)
    root = source.resolve(strict=True)
    verified_assets: dict[str, str] = {}
    for size_name, metadata_key in (("small", "icon_small"), ("large", "icon_large")):
        expected = registered["assets"][size_name]
        relative_text = icon_paths[metadata_key]
        if relative_text != expected["openai_path"]:
            raise SurfaceSymbolError(f"Skill icon path drifted: {source.name}/{size_name}")
        relative = PurePosixPath(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            raise SurfaceSymbolError(f"Skill icon path escapes its source: {source.name}")
        asset = source.joinpath(*relative.parts).resolve(strict=True)
        if not asset.is_relative_to(root) or asset.is_symlink() or not asset.is_file():
            raise SurfaceSymbolError(f"Skill icon is not a local regular file: {source.name}")
        data = asset.read_bytes()
        width, height, bit_depth, color_type = _png_properties(data)
        if (
            width != expected["pixels"]
            or height != expected["pixels"]
            or bit_depth != 8
            or color_type != 6
        ):
            raise SurfaceSymbolError(f"Skill icon PNG properties drifted: {source.name}")
        digest = hashlib.sha256(data).hexdigest()
        if digest != expected["sha256"]:
            raise SurfaceSymbolError(f"Skill icon checksum drifted: {source.name}/{size_name}")
        verified_assets[size_name] = digest
    return {
        "entry_id": source.name,
        "symbol": registered["name"],
        "assets": verified_assets,
    }
