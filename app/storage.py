"""Datei-basierte Persistenz fuer hochgeladene iBOMs, Zustaende und Einstellungen.

Layout unter ``data/`` (per Docker-Volume gemountet):

    data/
      settings.json                  globale Einstellungen
      certs/                         self-signed TLS-Zertifikat
      iboms/<id>/
        ibom.html                    Original-Upload
        meta.json                    {id, name, filename, created, last_modified}
        state.json                   {checkboxes: {...}, scans: [...]}
"""
from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import ibom_processor
from .paths import DATA_DIR

IBOMS_DIR = DATA_DIR / "iboms"
SETTINGS_FILE = DATA_DIR / "settings.json"
CERTS_DIR = DATA_DIR / "certs"

DEFAULT_SETTINGS = {
    "sound": True,          # sound on successful scan (phone)
    "vibrate": True,        # vibrate on successful scan (phone)
    "scrollTo": True,       # scroll to the component in the viewer
    "highlightAlt": True,   # tint rows that have an alternative part
    "altColor": "#3b82f6",  # colour of the alternative marker (light blue)
    "highlightPlaced": True,  # tint placed rows subtly green
}

_MAX_SCANS = 500  # begrenzte Scan-History pro iBOM


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dirs() -> None:
    IBOMS_DIR.mkdir(parents=True, exist_ok=True)
    CERTS_DIR.mkdir(parents=True, exist_ok=True)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # atomar


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", name or "").strip("-").lower()
    return s[:40] or "ibom"


# ---------------------------------------------------------------------------
# iBOMs
# ---------------------------------------------------------------------------

def _ibom_dir(ibom_id: str) -> Path:
    return IBOMS_DIR / ibom_id


def create_ibom(filename: str, content: bytes, display_name: str | None = None) -> dict:
    """Speichert eine hochgeladene iBOM. Wirft ValueError, wenn es keine gueltige iBOM ist."""
    ensure_dirs()
    try:
        html = content.decode("utf-8")
    except UnicodeDecodeError:
        html = content.decode("utf-8", errors="replace")

    if ibom_processor.extract_pcbdata(html) is None:
        raise ValueError("File contains no readable iBOM data (pcbdata).")

    idx = ibom_processor.build_index(html)
    title = idx.get("meta", {}).get("title") or ""
    base_name = display_name or title or Path(filename).stem or "iBOM"

    ibom_id = f"{_slug(base_name)}-{uuid.uuid4().hex[:8]}"
    d = _ibom_dir(ibom_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "ibom.html").write_text(html, encoding="utf-8")

    now = _now()
    meta = {
        "id": ibom_id,
        "name": base_name,
        "filename": filename,
        "created": now,
        "last_modified": now,
        "component_count": idx.get("component_count", 0),
        "distinct_lcsc": idx.get("distinct_lcsc", 0),
        "lcsc_field": idx.get("lcsc_field"),
        "warning": idx.get("warning"),
    }
    _write_json(d / "meta.json", meta)
    _write_json(d / "state.json", {"checkboxes": {}, "scans": []})
    return meta


def list_iboms() -> list[dict]:
    ensure_dirs()
    items = []
    for d in IBOMS_DIR.iterdir():
        if not d.is_dir():
            continue
        meta = _read_json(d / "meta.json", None)
        if meta:
            items.append(meta)
    items.sort(key=lambda m: m.get("last_modified", ""), reverse=True)
    return items


def get_meta(ibom_id: str) -> dict | None:
    return _read_json(_ibom_dir(ibom_id) / "meta.json", None)


def get_html(ibom_id: str) -> str | None:
    path = _ibom_dir(ibom_id) / "ibom.html"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def rename_ibom(ibom_id: str, name: str) -> dict | None:
    meta = get_meta(ibom_id)
    if not meta:
        return None
    meta["name"] = name
    meta["last_modified"] = _now()
    _write_json(_ibom_dir(ibom_id) / "meta.json", meta)
    return meta


def delete_ibom(ibom_id: str) -> bool:
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True


def touch(ibom_id: str) -> None:
    meta = get_meta(ibom_id)
    if meta:
        meta["last_modified"] = _now()
        _write_json(_ibom_dir(ibom_id) / "meta.json", meta)


# ---------------------------------------------------------------------------
# Zustand (Checkboxen + Scan-History)
# ---------------------------------------------------------------------------

def get_state(ibom_id: str) -> dict:
    st = _read_json(_ibom_dir(ibom_id) / "state.json", {})
    st.setdefault("checkboxes", {})
    st.setdefault("scans", [])
    st.setdefault("alternatives", [])
    st.setdefault("phase", "sourcing")  # Pipeline-Phase: "sourcing" | "placing"
    return st


def set_phase(ibom_id: str, phase: str) -> str | None:
    if phase not in ("sourcing", "placing"):
        return None
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return None
    state = get_state(ibom_id)
    state["phase"] = phase
    _write_json(d / "state.json", state)
    return phase


def save_checkboxes(ibom_id: str, checkboxes: dict) -> None:
    """Speichert die Checkbox-Referenzen (z.B. {'Sourced': '3,7', 'Placed': ''})."""
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return
    state = get_state(ibom_id)
    state["checkboxes"] = {str(k): str(v) for k, v in (checkboxes or {}).items()}
    _write_json(d / "state.json", state)
    touch(ibom_id)


def add_scan(ibom_id: str, scan: dict) -> None:
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return
    state = get_state(ibom_id)
    scans = state.setdefault("scans", [])
    scans.append({**scan, "ts": _now()})
    if len(scans) > _MAX_SCANS:
        del scans[: len(scans) - _MAX_SCANS]
    _write_json(d / "state.json", state)


def add_alternative(ibom_id: str, alt: dict) -> dict | None:
    """Ordnet ein alternatives Bauteil einer BOM-Zielgruppe zu.

    ``alt`` erwartet: altLcsc, altMpn, altValue, altPackage, targetLcsc,
    refs, footprints. Vorhandene Eintraege mit gleicher (targetLcsc, altLcsc)
    werden ersetzt.
    """
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return None
    state = get_state(ibom_id)
    alts = state.setdefault("alternatives", [])
    alts[:] = [
        a for a in alts
        if not (a.get("targetLcsc") == alt.get("targetLcsc")
                and a.get("altLcsc") == alt.get("altLcsc"))
    ]
    entry = {**alt, "ts": _now()}
    alts.append(entry)
    _write_json(d / "state.json", state)
    touch(ibom_id)
    return entry


def remove_alternative(ibom_id: str, alt_lcsc: str, target_lcsc: str | None = None) -> None:
    d = _ibom_dir(ibom_id)
    if not d.exists():
        return
    state = get_state(ibom_id)
    alts = state.setdefault("alternatives", [])
    alts[:] = [
        a for a in alts
        if not (a.get("altLcsc") == alt_lcsc
                and (target_lcsc is None or a.get("targetLcsc") == target_lcsc))
    ]
    _write_json(d / "state.json", state)
    touch(ibom_id)


# ---------------------------------------------------------------------------
# Globale Einstellungen
# ---------------------------------------------------------------------------

def get_settings() -> dict:
    ensure_dirs()
    stored = _read_json(SETTINGS_FILE, {})
    if not isinstance(stored, dict):
        stored = {}
    # only known keys -> obsolete/removed settings are dropped automatically
    return {k: stored.get(k, v) for k, v in DEFAULT_SETTINGS.items()}


def save_settings(patch: dict) -> dict:
    settings = get_settings()
    for key, value in (patch or {}).items():
        if key in DEFAULT_SETTINGS:
            settings[key] = value
    _write_json(SETTINGS_FILE, settings)
    return settings
