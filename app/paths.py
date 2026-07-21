"""Central path resolution — works from source *and* as a PyInstaller bundle.

When frozen with PyInstaller (``--onefile``), bundled resources (``static/`` and
``templates/``) are unpacked into a temporary ``sys._MEIPASS`` directory that is
recreated on every run. Persistent runtime data (uploaded iBOMs, checkbox state,
TLS certificate, LCSC cache) must therefore live *next to the executable* so it
survives restarts — not inside that throwaway bundle.

Layouts:
    from source      resources + data under the repo root
    frozen exe       resources in sys._MEIPASS, data next to the .exe
    SCAN2PLACE_DATA  overrides the data directory in either case
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_FROZEN = getattr(sys, "frozen", False)

if _FROZEN:
    # PyInstaller unpacks bundled datas here (temporary, per run).
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    _default_data = Path(sys.executable).resolve().parent / "data"
else:
    RESOURCE_DIR = Path(__file__).resolve().parent.parent
    _default_data = RESOURCE_DIR / "data"

# Persistent data — overridable so users can point it anywhere they like.
DATA_DIR = Path(os.environ.get("SCAN2PLACE_DATA") or _default_data)

STATIC_DIR = RESOURCE_DIR / "static"
TEMPLATES_DIR = RESOURCE_DIR / "templates"
