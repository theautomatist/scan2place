"""Ruft Bauteil-Informationen zu einer LCSC-Teilenummer online ab.

Verwendet die LCSC-`ftps`-Detail-API (dieselbe Datenbasis wie lcsc.com /
JLCPCB). Ergebnisse werden dauerhaft in ``data/lcsc_cache/`` zwischengespeichert,
da sich die Bauteil-Stammdaten nicht aendern.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path

from . import values

_DETAIL_URL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={}"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_HEADERS = {
    "User-Agent": _UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.lcsc.com/",
}

_ctx = ssl.create_default_context()

# Cache-Verzeichnis (wird von storage gesetzt, hier ableitbar)
_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "lcsc_cache"

# Parameter-Feldname je Bauteiltyp
_VALUE_PARAM = {
    values.KIND_CAP: "capacitance",
    values.KIND_RES: "resistance",
    values.KIND_IND: "inductance",
}


def _cache_path(lcsc: str) -> Path:
    return _CACHE_DIR / f"{lcsc.upper()}.json"


def _fetch_raw(lcsc: str) -> dict | None:
    url = _DETAIL_URL.format(lcsc)
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=12, context=_ctx) as r:
        data = json.loads(r.read())
    if not data or not data.get("ok") or not data.get("result"):
        return None
    return data["result"]


def _extract(result: dict, lcsc: str) -> dict:
    cats = [c.get("catalogNameEn", "") for c in result.get("parentCatalogList", [])]
    category = " > ".join(c for c in cats if c)
    kind = None
    for c in cats:
        kind = values.kind_from_category(c) or kind
    if kind is None:
        kind = values.kind_from_category(result.get("productNameEn", ""))

    params = result.get("paramVOList") or result.get("productParamVOList") or []
    param_map = {}
    for p in params:
        name = (p.get("paramNameEn") or p.get("paramName") or "").strip().lower()
        val = p.get("paramValueEn") or p.get("paramValue")
        if name:
            param_map[name] = val

    package = result.get("encapStandard") or ""
    size = values.package_size(package) or values.package_size(result.get("productModel", ""))

    # Rohwert je nach Typ aus den strukturierten Parametern
    value_raw = None
    if kind and _VALUE_PARAM.get(kind) in param_map:
        value_raw = param_map[_VALUE_PARAM[kind]]
    value_num = values.parse_value(value_raw, kind) if value_raw else None

    return {
        "ok": True,
        "lcsc": lcsc.upper(),
        "mpn": result.get("productModel") or "",
        "description": result.get("productIntroEn") or result.get("productNameEn") or "",
        "category": category,
        "kind": kind,
        "package": package,
        "size": size,
        "value_raw": value_raw,
        "value_num": value_num,
    }


def fetch_part(lcsc: str, use_cache: bool = True) -> dict:
    """Liefert Bauteil-Infos zu einer LCSC-Nummer (mit Platten-Cache).

    Rueckgabe enthaelt immer ``ok`` (bool). Bei Fehlern zusaetzlich ``error``.
    """
    if not lcsc:
        return {"ok": False, "error": "no LCSC number"}
    lcsc = lcsc.strip().upper()

    path = _cache_path(lcsc)
    if use_cache and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    try:
        result = _fetch_raw(lcsc)
    except urllib.error.HTTPError as exc:
        return {"ok": False, "lcsc": lcsc, "error": f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "lcsc": lcsc, "error": f"network: {exc}"}
    except (json.JSONDecodeError, ValueError) as exc:
        return {"ok": False, "lcsc": lcsc, "error": f"invalid response: {exc}"}

    if result is None:
        info = {"ok": False, "lcsc": lcsc, "error": "not found"}
    else:
        info = _extract(result, lcsc)

    # nur erfolgreiche Ergebnisse cachen
    if info.get("ok"):
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return info
