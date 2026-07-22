"""FastAPI-Anwendung: iBOM-Verwaltung, Auslieferung mit Sync-Script und
WebSocket-Bruecke zwischen Smartphone-Scanner und PC-Viewer.
"""
from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import asyncio

from . import ibom_processor, lcsc_api, storage
from .paths import STATIC_DIR, TEMPLATES_DIR
from .qr import parse_qr
from .ws_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.ensure_dirs()
    yield


app = FastAPI(title="scan2place", lifespan=lifespan)

# In-Memory-Cache des LCSC-Index je iBOM (wird beim ersten Zugriff gefuellt).
_index_cache: dict[str, dict] = {}


def get_index(ibom_id: str) -> dict | None:
    if ibom_id in _index_cache:
        return _index_cache[ibom_id]
    html = storage.get_html(ibom_id)
    if html is None:
        return None
    index = ibom_processor.build_index(html)
    _index_cache[ibom_id] = index
    return index


def _invalidate(ibom_id: str) -> None:
    _index_cache.pop(ibom_id, None)


# ---------------------------------------------------------------------------
# Pipeline: Fortschritt & Phase
# ---------------------------------------------------------------------------

def _progress_payload(ibom_id: str) -> dict:
    index = get_index(ibom_id)
    state = storage.get_state(ibom_id)
    prog = (ibom_processor.compute_progress(index, state.get("checkboxes", {}))
            if index else {"total": 0, "sourced": 0, "placed": 0})
    return {
        "type": "progress",
        "phase": state.get("phase", "sourcing"),
        **prog,
    }


async def _broadcast_progress(ibom_id: str) -> None:
    msg = _progress_payload(ibom_id)
    await manager.send_to_role(ibom_id, "viewer", msg)
    await manager.send_to_role(ibom_id, "scanner", msg)


def _maybe_advance_phase(ibom_id: str) -> None:
    """Automatically switch from 'sourcing' to 'placing' once every position
    is sourced."""
    state = storage.get_state(ibom_id)
    if state.get("phase") != "sourcing":
        return
    index = get_index(ibom_id)
    if not index:
        return
    prog = ibom_processor.compute_progress(index, state.get("checkboxes", {}))
    if prog["total"] > 0 and prog["sourced"] >= prog["total"]:
        storage.set_phase(ibom_id, "placing")


# ---------------------------------------------------------------------------
# Seiten (Single-Page-App)
# ---------------------------------------------------------------------------

# Matches local static asset URLs (js/css) so we can append a cache-busting token.
_STATIC_REF_RE = re.compile(r"(/static/[^\"'?\s]+\.(?:js|css))")


def _cache_bust(html: str) -> str:
    """Append ?v=<mtime> to local /static js/css refs so browsers fetch the
    current version after every rebuild instead of a stale cached copy."""
    def repl(m: "re.Match[str]") -> str:
        ref = m.group(1)
        path = STATIC_DIR / ref[len("/static/"):]
        try:
            return f"{ref}?v={int(path.stat().st_mtime)}"
        except OSError:
            return ref
    return _STATIC_REF_RE.sub(repl, html)


@app.get("/", response_class=HTMLResponse)
@app.get("/scan", response_class=HTMLResponse)
@app.get("/viewer", response_class=HTMLResponse)
def index() -> HTMLResponse:
    html = (TEMPLATES_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(_cache_bust(html))


# ---------------------------------------------------------------------------
# REST-API: iBOMs
# ---------------------------------------------------------------------------

@app.get("/api/iboms")
def api_list_iboms():
    return {"iboms": storage.list_iboms()}


@app.post("/api/iboms")
async def api_upload_ibom(file: UploadFile = File(...)):
    content = await file.read()
    try:
        meta = storage.create_ibom(file.filename or "ibom.html", content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    get_index(meta["id"])  # Index vorwaermen -> Fortschritt sofort verfuegbar
    return meta


@app.get("/api/iboms/{ibom_id}")
def api_get_ibom(ibom_id: str):
    meta = storage.get_meta(ibom_id)
    if not meta:
        raise HTTPException(status_code=404, detail="iBOM not found")
    return meta


@app.patch("/api/iboms/{ibom_id}")
async def api_rename_ibom(ibom_id: str, body: dict):
    name = (body or {}).get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name missing")
    meta = storage.rename_ibom(ibom_id, name)
    if not meta:
        raise HTTPException(status_code=404, detail="iBOM not found")
    return meta


@app.delete("/api/iboms/{ibom_id}")
def api_delete_ibom(ibom_id: str):
    if not storage.delete_ibom(ibom_id):
        raise HTTPException(status_code=404, detail="iBOM not found")
    _invalidate(ibom_id)
    return {"ok": True}


@app.get("/api/iboms/{ibom_id}/state")
def api_get_state(ibom_id: str):
    if not storage.get_meta(ibom_id):
        raise HTTPException(status_code=404, detail="iBOM not found")
    return storage.get_state(ibom_id)


@app.get("/api/iboms/{ibom_id}/progress")
def api_get_progress(ibom_id: str):
    if not storage.get_meta(ibom_id):
        raise HTTPException(status_code=404, detail="iBOM not found")
    return _progress_payload(ibom_id)


@app.put("/api/iboms/{ibom_id}/phase")
async def api_set_phase(ibom_id: str, body: dict):
    if not storage.get_meta(ibom_id):
        raise HTTPException(status_code=404, detail="iBOM not found")
    if storage.set_phase(ibom_id, (body or {}).get("phase")) is None:
        raise HTTPException(status_code=400, detail="invalid phase")
    await _broadcast_progress(ibom_id)
    return {"ok": True, "phase": (body or {}).get("phase")}


@app.get("/api/iboms/{ibom_id}/lookup")
def api_lookup(ibom_id: str, code: str):
    """Parst einen (rohen) QR-Code oder eine LCSC-Nummer und sucht sie in der iBOM.

    Praktisch fuer manuelle Eingabe / Debugging ohne WebSocket.
    """
    index = get_index(ibom_id)
    if index is None:
        raise HTTPException(status_code=404, detail="iBOM not found")
    qr = parse_qr(code)
    result = ibom_processor.lookup(index, qr["lcsc"]) if qr["lcsc"] else None
    return {
        "ok": bool(result),
        "lcsc": qr["lcsc"],
        "name": qr["name"],
        "qty": qr["qty"],
        "result": result,
        "raw": qr["raw"],
    }


# ---------------------------------------------------------------------------
# Auslieferung der iBOM (mit injiziertem Sync-Script) fuer den iframe
# ---------------------------------------------------------------------------

@app.get("/ibom/{ibom_id}", response_class=HTMLResponse)
def serve_ibom(ibom_id: str):
    html = storage.get_html(ibom_id)
    if html is None:
        raise HTTPException(status_code=404, detail="iBOM not found")
    state = storage.get_state(ibom_id)
    settings = storage.get_settings()
    injected = ibom_processor.inject_helper(
        html, ibom_id, state.get("checkboxes", {}), settings,
        state.get("alternatives", []),
    )
    # Kein Caching, damit der jeweils aktuelle Zustand ausgeliefert wird.
    return HTMLResponse(injected, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------------
# Einstellungen (global)
# ---------------------------------------------------------------------------

@app.get("/api/settings")
def api_get_settings():
    return storage.get_settings()


@app.put("/api/settings")
async def api_put_settings(body: dict):
    settings = storage.save_settings(body or {})
    # push updated settings to open viewers/scanners live
    await manager.broadcast_all({"type": "settings", "settings": settings})
    return settings


# ---------------------------------------------------------------------------
# WebSocket-Bruecke
# ---------------------------------------------------------------------------

def _find_adopted_alternative(state: dict, lcsc: str) -> dict | None:
    """Find an already-adopted alternative by its scanned LCSC number."""
    key = str(lcsc).strip().upper()
    for a in state.get("alternatives", []):
        if str(a.get("altLcsc", "")).strip().upper() == key:
            return a
    return None


async def _handle_scan(ws: WebSocket, ibom_id: str, data: dict) -> None:
    payload = data.get("payload") or data.get("code") or ""
    qr = parse_qr(payload)
    index = get_index(ibom_id)
    lcsc = qr["lcsc"]
    state = storage.get_state(ibom_id)
    phase = state.get("phase", "sourcing")

    result = ibom_processor.lookup(index, lcsc) if (index and lcsc) else None
    # An already-adopted alternative isn't in the BOM index, but scanning it must
    # behave like a hit on its target position — especially while placing, where the
    # phone should ask "placed?" instead of offering to adopt it again.
    if not result and lcsc:
        adopted = _find_adopted_alternative(state, lcsc)
        if adopted:
            result = {
                "lcsc": lcsc,
                "refs": adopted.get("refs", []),
                "footprints": adopted.get("footprints", []),
                "value": adopted.get("altValue", ""),
                "package": adopted.get("altPackage", ""),
                "mfr": adopted.get("altMpn", ""),
                "count": len(adopted.get("refs", [])),
            }

    part_info = None
    candidates = []
    needs_confirm = False
    check_sourced = False
    check_placed = False
    if result:
        matched = "exact"
        if phase == "placing":
            needs_confirm = True   # phone confirms "all placed?" before ticking
        else:
            check_sourced = True   # sourcing phase -> tick Sourced directly
    elif index and lcsc and phase != "placing":
        # Sourcing only: unknown part -> look it up online and offer alternatives.
        # "Adopt as alternative?" makes no sense while placing, so it's skipped there.
        # The network call is blocking -> run it off the event loop.
        part_info = await asyncio.to_thread(lcsc_api.fetch_part, lcsc)
        candidates = ibom_processor.match_alternatives(index, part_info)
        matched = "alternative" if candidates else "none"
        if candidates:
            # Attach target-position status (already ticked?) as a decision aid.
            cbx = state.get("checkboxes", {})
            sourced = {x for x in (cbx.get("Sourced") or "").split(",") if x}
            placed = {x for x in (cbx.get("Placed") or "").split(",") if x}
            for c in candidates:
                fps = [str(f) for f in c.get("footprints", [])]
                c["already_sourced"] = bool(fps) and all(f in sourced for f in fps)
                c["already_placed"] = bool(fps) and all(f in placed for f in fps)
    else:
        matched = "none"

    # 1) Rueckmeldung an das scannende Smartphone
    await ws.send_json({
        "type": "scan_result",
        "matched": matched,
        "ok": bool(result),
        "lcsc": lcsc,
        "name": qr["name"],
        "qty": qr["qty"],
        "result": result,
        "part_info": part_info,
        "candidates": candidates,
        "phase": phase,
        "needs_confirm": needs_confirm,
        "raw": qr["raw"],
    })

    # 2) Gescannte Position im Viewer immer sofort hervorheben.
    if result and not needs_confirm:
        # Sourcing / direkter Treffer: hervorheben + abhaken (+ Ack an den Scanner).
        await manager.send_to_role(ibom_id, "viewer", {
            "type": "scan",
            "lcsc": lcsc,
            "name": qr["name"],
            "qty": qr["qty"],
            "result": result,
            "check_sourced": check_sourced,
            "check_placed": check_placed,
        })
    elif result and needs_confirm:
        # Placing: nur hervorheben + hinscrollen. Das Placed-Haekchen folgt erst
        # nach der Bestaetigung ueber confirm_placed.
        await manager.send_to_role(ibom_id, "viewer", {
            "type": "highlight",
            "lcsc": lcsc,
            "result": result,
        })
    elif candidates:
        # Alternative vorgeschlagen: beste Zielposition schon hervorheben, damit man
        # sieht, wohin sie passt — abgehakt wird erst nach dem Uebernehmen.
        best = candidates[0]
        await manager.send_to_role(ibom_id, "viewer", {
            "type": "highlight",
            "lcsc": lcsc,
            "result": {"footprints": best.get("footprints", []), "refs": best.get("refs", [])},
        })

    # 3) In die Scan-History schreiben
    storage.add_scan(ibom_id, {
        "lcsc": lcsc,
        "name": qr["name"],
        "qty": qr["qty"],
        "found": bool(result),
        "matched": matched,
        "refs": result["refs"] if result else [],
    })


async def _handle_set_alternative(ws: WebSocket, ibom_id: str, data: dict) -> None:
    """Speichert eine vom Nutzer bestaetigte Alternative und meldet sie an die Viewer."""
    alt = {
        "altLcsc": data.get("altLcsc"),
        "altMpn": data.get("altMpn"),
        "altValue": data.get("altValue"),
        "altPackage": data.get("altPackage"),
        "targetLcsc": data.get("targetLcsc"),
        "refs": data.get("refs", []),
        "footprints": data.get("footprints", []),
    }
    if not alt["altLcsc"] or not alt["footprints"]:
        return
    entry = storage.add_alternative(ibom_id, alt)

    # Adopting an alternative means the part is sourced.
    await manager.send_to_role(ibom_id, "viewer", {
        "type": "alternative",
        "alt": entry,
        "check_sourced": True,
        "check_placed": False,
    })
    # Bestaetigung ans Smartphone
    await ws.send_json({
        "type": "alternative_saved",
        "altLcsc": alt["altLcsc"],
        "refs": alt["refs"],
    })


async def _handle_confirm_placed(ibom_id: str, data: dict) -> None:
    """Placing-Phase: das Handy hat bestaetigt, dass alle Bauteile platziert sind."""
    footprints = data.get("footprints", [])
    if not footprints:
        return
    await manager.send_to_role(ibom_id, "viewer", {
        "type": "scan",
        "lcsc": data.get("lcsc"),
        "result": {
            "footprints": footprints,
            "refs": data.get("refs", []),
            "value": data.get("value"),
        },
        "check_sourced": False,
        "check_placed": True,
    })


@app.websocket("/ws/{role}/{ibom_id}")
async def ws_endpoint(ws: WebSocket, role: str, ibom_id: str):
    if role not in ("viewer", "scanner"):
        await ws.close(code=4003)
        return

    await manager.connect(ws, ibom_id, role)
    # aktuelle Einstellungen + Fortschritt direkt nach Verbindungsaufbau senden
    await ws.send_json({"type": "settings", "settings": storage.get_settings()})
    await ws.send_json(_progress_payload(ibom_id))
    try:
        while True:
            data = await ws.receive_json()
            mtype = data.get("type")

            if role == "scanner" and mtype == "scan":
                await _handle_scan(ws, ibom_id, data)

            elif role == "scanner" and mtype == "set_alternative":
                await _handle_set_alternative(ws, ibom_id, data)

            elif role == "scanner" and mtype == "confirm_placed":
                await _handle_confirm_placed(ibom_id, data)

            elif role == "viewer" and mtype == "checkbox_state":
                storage.save_checkboxes(ibom_id, data.get("checkboxes", {}))
                _maybe_advance_phase(ibom_id)
                await _broadcast_progress(ibom_id)

            elif role == "viewer" and mtype == "remove_alternative":
                storage.remove_alternative(ibom_id, data.get("altLcsc"), data.get("targetLcsc"))

            elif role == "viewer" and mtype == "scan_ack":
                # Bestaetigung des Viewers zurueck ans Smartphone
                await manager.send_to_role(ibom_id, "scanner", {
                    "type": "viewer_ack",
                    "lcsc": data.get("lcsc"),
                    "found": data.get("found"),
                    "refs": data.get("refs", []),
                    "checked": data.get("checked", []),
                })
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(ws, ibom_id, role)
        await manager.broadcast_presence(ibom_id)


# statische Dateien
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# Start (mit optionalem HTTPS)
# ---------------------------------------------------------------------------

def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8090"))
    use_https = os.environ.get("USE_HTTPS", "1") not in ("0", "false", "False", "")

    storage.ensure_dirs()

    if not use_https:
        import uvicorn
        print(f"\n  scan2place is running at http://<your-ip>:{port}\n")
        uvicorn.run(app, host=host, port=port)
        return

    import asyncio

    from .certs import ensure_cert
    from .dualstack import serve_dual

    cert_path = storage.CERTS_DIR / "cert.pem"
    key_path = storage.CERTS_DIR / "key.pem"
    ensure_cert(cert_path, key_path)

    print(f"\n  scan2place is running at https://<your-ip>:{port}")
    print(f"  (http://<your-ip>:{port} is redirected to https automatically)\n")
    asyncio.run(serve_dual(app, host, port, cert_path, key_path))


if __name__ == "__main__":
    main()
