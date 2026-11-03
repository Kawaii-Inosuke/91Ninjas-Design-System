"""Where 91 Studio keeps its data, and the numbers that work in the infographic skill (D-010: ported, not imported).

Everything is local (D-009): `STUDIO_DATA_DIR`, else `$XDG_DATA_HOME/91studio`, else `~/.local/share/91studio`.
    clients/<client>/   installed client packs (DESIGN.md, critic_rules.md, assets/, past/, source/)
    runs/<run>/         one folder per design run (spec, HTML, PNG versions)
    history.db          approved designs and the duplicate-topic index
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_dir() -> Path:
    if d := os.environ.get("STUDIO_DATA_DIR"):
        return Path(d)
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "91studio"


def clients_dir() -> Path:
    return data_dir() / "clients"


def runs_dir() -> Path:
    return data_dir() / "runs"


def history_db() -> Path:
    return data_dir() / "history.db"


def models_dir() -> Path:
    """The embedding model (~90 MB, downloaded once): `STUDIO_MODELS_DIR`, else <data>/models."""
    return Path(os.environ["STUDIO_MODELS_DIR"]) if os.environ.get("STUDIO_MODELS_DIR") else data_dir() / "models"


def fonts_dir() -> Path:
    """Client fonts are installed here so the editor (system fonts) can draw them (PHASE-1 issue 2): the user's own
    fonts folder of each system (D-040): Linux ~/.local/share/fonts/91studio, macOS ~/Library/Fonts/91studio,
    Windows %LOCALAPPDATA%\\Microsoft\\Windows\\Fonts (registered by `packs.install_fonts`)."""
    if os.environ.get("STUDIO_FONTS_DIR"):
        return Path(os.environ["STUDIO_FONTS_DIR"])
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Fonts" / "91studio"
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Microsoft" / "Windows" / "Fonts"
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "fonts" / "91studio"


# Similar-design check (the skill's warn threshold, calibrated on real client work).
DEDUP_WARN = 0.65         # a design this similar is shown with its files; the designer decides (D-020)
LAYOUT_WINDOW = 4         # avoid layouts used in the last N approved designs (a brand can override)
PHASH_MAX_DISTANCE = 8    # visual near-duplicate when the perceptual hashes differ by at most this

# Rendering and critique.
DEFAULT_CANVAS_WIDTH = 1080
MAX_CANVAS_HEIGHT = 6000
MAX_REVISIONS = 3         # critic rounds before the designer sees the best version
PREVIEW_WIDTH = 960       # the critic looks at a downscaled preview (fewer tokens)
MIN_TEXT_PX = 20          # smaller text is flagged

# Client packs (SECURITY T10, T12).
PACK_MAX_BYTES = 100 * 1024 * 1024
PACK_MAX_FILES = 500
PACK_PAST_MAX = 10        # sample past infographics included in an exported pack
PACK_PAST_MIN = 3         # a new client needs at least this many
