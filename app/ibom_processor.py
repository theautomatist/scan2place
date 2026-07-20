"""Liest und modifiziert von KiCad's InteractiveHtmlBom erzeugte iBOM-HTML.

Aufgaben:
  * ``pcbdata`` (LZString-komprimiert) und ``config`` aus der HTML extrahieren.
  * Das LCSC-Feld robust erkennen (per Feldname *oder* per Wertemuster), damit
    beliebige iBOMs funktionieren.
  * Einen Index  LCSC-Nummer -> {Referenzen, Footprint-Indizes, Value, ...}
    aufbauen (fuer Server-seitiges Scanner-Feedback).
  * Beim Ausliefern das Sync-/Scan-Script und den gespeicherten Checkbox-Zustand
    in die HTML injizieren.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import lzstring

from . import values

_LZ = lzstring.LZString()
_INJECT_JS = Path(__file__).resolve().parent.parent / "static" / "inject.js"


def _inject_version() -> int:
    """mtime of inject.js as a cache-busting token (falls back to 1)."""
    try:
        return int(_INJECT_JS.stat().st_mtime)
    except OSError:
        return 1

# LCSC-artiger Wert: 'C' + mindestens 3 Ziffern.
_LCSC_VALUE_RE = re.compile(r"^C\d{3,}$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Extraktion von config / pcbdata
# ---------------------------------------------------------------------------

def _decode_json_after(text: str, marker: str):
    """Findet ``marker`` und dekodiert das direkt danach folgende JSON-Objekt/-Array."""
    i = text.find(marker)
    if i == -1:
        return None
    i += len(marker)
    while i < len(text) and text[i] not in "{[":
        i += 1
    if i >= len(text):
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text, i)
        return obj
    except json.JSONDecodeError:
        return None


def extract_config(html: str):
    return _decode_json_after(html, "var config = ")


def extract_pcbdata(html: str):
    """Liest ``pcbdata`` — bevorzugt LZString-komprimiert, sonst als reines JSON."""
    m = re.search(
        r'var pcbdata = JSON\.parse\(LZString\.decompressFromBase64\("([^"]*)"\)\)',
        html,
    )
    if m:
        decompressed = _LZ.decompressFromBase64(m.group(1))
        return json.loads(decompressed)
    # Aeltere iBOMs: unkomprimiertes 'var pcbdata = {...}'
    return _decode_json_after(html, "var pcbdata = ")


# ---------------------------------------------------------------------------
# Feld-Erkennung
# ---------------------------------------------------------------------------

def _field_index_by_name(fields, *keywords, default=None):
    for idx, name in enumerate(fields):
        low = str(name).lower()
        if any(k in low for k in keywords):
            return idx
    return default


def detect_lcsc_field_index(config, pcbdata):
    """Ermittelt den Spaltenindex des LCSC-Feldes.

    Strategie: 1) Feldname enthaelt 'lcsc'  2) sonst die Spalte, deren Werte
    mehrheitlich dem LCSC-Muster (C + Ziffern) entsprechen.
    """
    fields = (config or {}).get("fields", []) or []

    named = _field_index_by_name(fields, "lcsc")
    if named is not None:
        return named

    bom_fields = (pcbdata or {}).get("bom", {}).get("fields", {}) or {}
    rows = list(bom_fields.values())
    if not rows:
        return None

    num_cols = max((len(r) for r in rows), default=0)
    counts = [0] * num_cols
    for row in rows:
        for idx in range(min(num_cols, len(row))):
            if _LCSC_VALUE_RE.match(str(row[idx]).strip()):
                counts[idx] += 1

    total = len(rows)
    best_idx, best_score = None, 0.0
    for idx in range(num_cols):
        score = counts[idx] / total
        if score > best_score:
            best_idx, best_score = idx, score
    return best_idx if best_score >= 0.5 else None


# ---------------------------------------------------------------------------
# LCSC-Index
# ---------------------------------------------------------------------------

def _bom_groups(bom):
    """Alle BOM-Gruppen unabhaengig von der Layer-Ansicht."""
    both = bom.get("both")
    if both:
        return both
    return list(bom.get("F", []) or []) + list(bom.get("B", []) or [])


def build_index(html: str) -> dict:
    """Baut den kompletten Lookup-Index aus einer iBOM-HTML."""
    config = extract_config(html) or {}
    pcbdata = extract_pcbdata(html)
    if not pcbdata:
        return {
            "lcsc": {}, "meta": {}, "fields": [], "lcsc_field": None,
            "checkboxes": [], "component_count": 0,
            "warning": "could not read pcbdata",
        }

    fields = config.get("fields", []) or []
    lcsc_idx = detect_lcsc_field_index(config, pcbdata)
    value_idx = _field_index_by_name(fields, "value", default=0)
    package_idx = _field_index_by_name(fields, "package", "footprint")
    mfr_idx = _field_index_by_name(fields, "mfr", "manufacturer", "mpn")

    bom = pcbdata.get("bom", {}) or {}
    bom_fields = bom.get("fields", {}) or {}

    def get_row(fp):
        return bom_fields.get(str(fp)) or bom_fields.get(fp)

    def cell(row, idx):
        if idx is None or idx >= len(row):
            return ""
        return str(row[idx] if row[idx] is not None else "")

    index: dict[str, dict] = {}
    positions: list[list] = []  # alle BOM-Positionen als Footprint-Listen (fuer Fortschritt)
    for group in _bom_groups(bom):
        if not group:
            continue
        positions.append([pair[1] for pair in group])
        if lcsc_idx is None:
            continue
        row = get_row(group[0][1])
        if not row or lcsc_idx >= len(row):
            continue
        lcsc = str(row[lcsc_idx] or "").strip().upper()
        if not lcsc:
            continue
        val = cell(row, value_idx)
        pkg = cell(row, package_idx)
        kind = values.kind_from_bom_package(pkg)
        entry = index.setdefault(lcsc, {
            "refs": [],
            "footprints": [],
            "value": val,
            "package": pkg,
            "mfr": cell(row, mfr_idx),
            "kind": kind,
            "size": values.package_size(pkg),
            "value_num": values.parse_value(val, kind),
        })
        for pair in group:
            entry["refs"].append(pair[0])
            entry["footprints"].append(pair[1])

    meta = pcbdata.get("metadata", {}) or {}
    checkboxes = [c.strip() for c in str(config.get("checkboxes", "")).split(",") if c.strip()]

    return {
        "lcsc": index,
        "meta": {
            "title": meta.get("title", ""),
            "revision": meta.get("revision", ""),
            "company": meta.get("company", ""),
            "date": meta.get("date", ""),
        },
        "fields": fields,
        "lcsc_field": fields[lcsc_idx] if lcsc_idx is not None and lcsc_idx < len(fields) else None,
        "checkboxes": checkboxes or ["Sourced", "Placed"],
        "positions": positions,
        "position_count": len(positions),
        "component_count": sum(len(e["refs"]) for e in index.values()),
        "distinct_lcsc": len(index),
        "warning": None if lcsc_idx is not None else "No LCSC field found in this iBOM",
    }


def compute_progress(index: dict, checkboxes: dict) -> dict:
    """Zaehlt fertige BOM-Positionen (Zeilen) je Checkbox.

    Eine Position gilt als 'sourced'/'placed', wenn ALLE ihre Footprints in der
    jeweiligen Checkbox-Menge stehen.
    """
    positions = index.get("positions", []) if index else []
    sourced = {x for x in (checkboxes.get("Sourced") or "").split(",") if x}
    placed = {x for x in (checkboxes.get("Placed") or "").split(",") if x}

    def done(fps: set) -> int:
        return sum(1 for pos in positions if pos and all(str(f) in fps for f in pos))

    return {"total": len(positions), "sourced": done(sourced), "placed": done(placed)}


def lookup(index: dict, lcsc: str) -> dict | None:
    """Sucht eine LCSC-Nummer im Index (case-insensitiv)."""
    if not lcsc:
        return None
    entry = index.get("lcsc", {}).get(lcsc.strip().upper())
    if not entry:
        return None
    return {
        "lcsc": lcsc.strip().upper(),
        "refs": entry["refs"],
        "footprints": entry["footprints"],
        "value": entry["value"],
        "package": entry["package"],
        "mfr": entry["mfr"],
        "count": len(entry["refs"]),
    }


def match_alternatives(index: dict, part_info: dict) -> list[dict]:
    """Findet BOM-Teile, die als Alternative zum gescannten Teil passen.

    ``part_info`` stammt aus ``lcsc_api.fetch_part`` und enthaelt
    kind/size/value_num. Ein Kandidat passt, wenn Bauteiltyp und Wert
    uebereinstimmen; Gehaeuse-Groesse ist ein zusaetzliches (bevorzugtes) Signal.
    """
    if not part_info or not part_info.get("ok"):
        return []
    p_kind = part_info.get("kind")
    p_size = part_info.get("size")
    p_val = part_info.get("value_num")
    if not p_kind or p_val is None:
        return []  # ohne Typ+Wert kein sinnvoller Abgleich (z.B. ICs)

    candidates = []
    for lcsc, e in index.get("lcsc", {}).items():
        if e.get("kind") != p_kind:
            continue
        if not values.values_match(p_val, e.get("value_num")):
            continue
        size_match = bool(p_size and e.get("size") and p_size == e["size"])
        candidates.append({
            "original_lcsc": lcsc,
            "refs": e["refs"],
            "footprints": e["footprints"],
            "value": e["value"],
            "package": e["package"],
            "size": e.get("size"),
            "size_match": size_match,
            "score": 2 if size_match else 1,
        })

    candidates.sort(key=lambda c: (-c["score"], c["refs"][0] if c["refs"] else ""))
    return candidates


def storage_prefix(meta: dict) -> str:
    """Reproduziert den localStorage-Prefix der iBOM."""
    return f"KiCad_HTML_BOM__{meta.get('title', '')}__{meta.get('revision', '')}__#"


# ---------------------------------------------------------------------------
# Injektion beim Ausliefern
# ---------------------------------------------------------------------------

def inject_helper(html: str, ibom_id: str, state: dict, settings: dict,
                  alternatives: list | None = None) -> str:
    """Fuegt Konfig-Blob + Sync-Script vor ``</body>`` ein.

    ``state`` enthaelt die gespeicherten Checkbox-Referenzen je Checkbox-Name,
    z.B. ``{"Sourced": "3,7,42", "Placed": ""}`` (komma-getrennte Footprint-Indizes).
    ``alternatives`` ist die Liste der zugeordneten Ersatzteile.
    """
    payload = {
        "ibomId": ibom_id,
        "state": state or {},
        "settings": settings or {},
        "alternatives": alternatives or [],
    }
    snippet = (
        "\n<script>window.__LCSC_HELPER__ = "
        + json.dumps(payload)
        + ";</script>\n"
        + f'<script src="/static/inject.js?v={_inject_version()}"></script>\n'
    )
    idx = html.rfind("</body>")
    if idx == -1:
        return html + snippet
    return html[:idx] + snippet + html[idx:]
