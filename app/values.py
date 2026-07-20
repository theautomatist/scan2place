"""Normalisierung von Bauteilwerten und Gehaeuse-Groessen.

Dient dem Abgleich zwischen BOM-Werten (z.B. "470nF", "4.7k", "10uH") und den
von der LCSC-API gelieferten Werten (z.B. "470nF", "1.8kΩ", "2nH").
"""
from __future__ import annotations

import re

# SI-Praefixe -> Faktor
_SI = {
    "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "R": 1.0, "r": 1.0,
}

# Bauteiltyp aus BOM-Package-Praefix bzw. aus LCSC-Kategorie
KIND_CAP = "C"
KIND_RES = "R"
KIND_IND = "L"


def kind_from_bom_package(package: str) -> str | None:
    """'C0402' -> 'C', 'R0402' -> 'R', 'L0603' -> 'L'."""
    if not package:
        return None
    first = package.strip()[:1].upper()
    if first in (KIND_CAP, KIND_RES, KIND_IND):
        return first
    return None


def kind_from_category(category: str) -> str | None:
    low = (category or "").lower()
    if "capacitor" in low:
        return KIND_CAP
    if "resistor" in low:
        return KIND_RES
    if "inductor" in low or "coil" in low or "choke" in low:
        return KIND_IND
    return None


def package_size(text: str) -> str | None:
    """Extrahiert die Gehaeuse-Groesse als 4-stelligen Imperial-Code.

    'C0402' -> '0402', '0402' -> '0402', '01005' -> '01005',
    'L0603' -> '0603'. Gibt None, wenn keine Standardgroesse erkennbar.
    """
    if not text:
        return None
    t = str(text).upper()
    m = re.search(r"\b(01005|0201|0402|0603|0805|1206|1210|1812|2010|2512|1008|1806)\b", t)
    if m:
        return m.group(1)
    # Praefix + 4 Ziffern (z.B. C0402, R0603)
    m = re.search(r"[CRLD](\d{4})", t)
    if m:
        return m.group(1)
    return None


def parse_value(s, kind: str | None = None) -> float | None:
    """Wandelt einen Wert-String in die SI-Basiseinheit (F / H / Ω) um.

    Beispiele:
        '470nF' -> 4.7e-07 ,  '1uF' -> 1e-06 ,  '2nH' -> 2e-09 ,
        '1.8k'  -> 1800    ,  '100kΩ' -> 100000 , '4.7k' -> 4700 ,
        '120'   -> 120     ,  '10uH' -> 1e-05
    Gibt None, wenn nichts Sinnvolles erkennbar ist.
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    # Einheiten-/Sonderzeichen vereinheitlichen
    t = t.replace("µ", "u").replace("μ", "u").replace("Ω", "").replace("Ω", "")
    t = re.sub(r"(?i)ohm", "", t)
    # abschliessende Einheit F/H/Hz entfernen (Praefix bleibt erhalten)
    t = re.sub(r"(?i)(F|H|Hz)$", "", t).strip()
    t = t.replace(" ", "")

    # Standard: Zahl + optionaler Praefix  (470n, 4.7k, 120, 1.8k)
    m = re.fullmatch(r"([\d.]+)([pnumkKMGRr]?)", t)
    if m:
        try:
            num = float(m.group(1))
        except ValueError:
            return None
        pref = m.group(2)
        return num * (_SI.get(pref, 1.0) if pref else 1.0)

    # RKM-Notation: 4k7 -> 4.7k , 1R5 -> 1.5 , 2N0 -> 2.0n
    m = re.fullmatch(r"(\d+)([pnumkKMGRr])(\d+)", t)
    if m:
        try:
            num = float(f"{m.group(1)}.{m.group(3)}")
        except ValueError:
            return None
        return num * _SI.get(m.group(2), 1.0)

    return None


def values_match(a: float | None, b: float | None, rel_tol: float = 0.02) -> bool:
    """Vergleicht zwei Basiswerte mit relativer Toleranz (Default 2%)."""
    if a is None or b is None:
        return False
    if a == 0 and b == 0:
        return True
    if a == 0 or b == 0:
        return abs(a - b) < 1e-15
    return abs(a - b) / max(abs(a), abs(b)) <= rel_tol
