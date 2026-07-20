"""Parser fuer LCSC/JLCPCB-Verpackungs-QR-Codes.

Typisches Format (pseudo-JSON ohne Anfuehrungszeichen), z.B.:

    {pbn:PICK2607040232,on:WM2607040155,pc:C2906290,pm:TYPE-C 16P CB1.6 073,qty:15,mc:,cc:1,pdi:223523636,hp:11,wc:ZH}

Entscheidend ist das Feld ``pc`` = LCSC-Teilenummer (beginnt mit 'C'). Diese
wird gegen das LCSC-Feld der iBOM gematcht.
"""
from __future__ import annotations

import re

# Reine LCSC-Nummer: 'C' gefolgt von mindestens 3 Ziffern.
_LCSC_RE = re.compile(r"^C\d{3,}$", re.IGNORECASE)
# Ein 'pc:Cxxxx'-Vorkommen irgendwo im String (Fallback fuer abweichende Formate).
_PC_IN_TEXT_RE = re.compile(r"\bpc\s*[:=]\s*(C\d{3,})", re.IGNORECASE)


def parse_qr(payload: str) -> dict:
    """Zerlegt einen QR-Payload in seine Felder.

    Rueckgabe: dict mit allen erkannten Rohfeldern plus normalisierten Werten:
        - ``lcsc`` : LCSC-Teilenummer (aus ``pc``), Grossbuchstaben, oder None
        - ``name`` : Bauteilbeschreibung (aus ``pm``) oder None
        - ``qty``  : Menge als int (aus ``qty``) oder None
        - ``raw``  : Original-Payload
    """
    raw = (payload or "").strip()
    fields: dict[str, str] = {}

    inner = raw
    if inner.startswith("{"):
        inner = inner[1:]
    if inner.endswith("}"):
        inner = inner[:-1]

    # Standard: komma-getrennte key:value-Paare. LCSC-Codes verwenden in den
    # Werten selbst keine Kommas, daher ist das robust genug.
    for part in inner.split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        if key:
            fields[key] = value.strip()

    lcsc = fields.get("pc")

    # Fallbacks fuer abweichende Codes:
    if not lcsc:
        m = _PC_IN_TEXT_RE.search(raw)
        if m:
            lcsc = m.group(1)
    if not lcsc and _LCSC_RE.match(raw):
        # nackte LCSC-Nummer ohne umschliessendes Format
        lcsc = raw

    if lcsc:
        lcsc = lcsc.strip().upper()

    qty = None
    if fields.get("qty"):
        digits = re.sub(r"[^\d]", "", fields["qty"])
        if digits:
            qty = int(digits)

    return {
        **fields,
        "lcsc": lcsc,
        "name": fields.get("pm") or None,
        "qty": qty,
        "raw": raw,
    }
