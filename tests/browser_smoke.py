"""End-to-End-Rauchtest OHNE Browser-Extension.

Startet einen echten (headless) Browser via Playwright, oeffnet die vom Server
injizierte iBOM, simuliert einen Smartphone-Scan ueber WebSocket und prueft, ob
das Bauteil im iBOM-DOM tatsaechlich hervorgehoben und abgehakt wird.

Voraussetzung: der Server laeuft bereits (z.B. `python -m app.main`).

Nutzung:
    pip install playwright websockets
    # Server starten (in eigenem Terminal):  USE_HTTPS=0 PORT=8077 python -m app.main
    python tests/browser_smoke.py --url http://127.0.0.1:8077 \
        --ibom "D:/Projects/FloraSense/electronics/Flora v0.4/ibom/ibom.html" \
        --lcsc C2906290 --expect USB1

Der Browser-Kanal ist standardmaessig 'msedge' (unter Windows immer vorhanden),
alternativ --channel chrome oder --channel chromium (dann `playwright install chromium`).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
import uuid

import websockets
from playwright.async_api import async_playwright


def _http(method: str, url: str, body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def upload(http_base: str, path: str) -> dict:
    boundary = uuid.uuid4().hex
    data = open(path, "rb").read()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="ibom.html"\r\n'
        f"Content-Type: text/html\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return _http("POST", f"{http_base}/api/iboms", body,
                 {"Content-Type": f"multipart/form-data; boundary={boundary}"})


def delete(http_base: str, ibom_id: str) -> None:
    try:
        _http("DELETE", f"{http_base}/api/iboms/{ibom_id}")
    except Exception:
        pass


async def send_scan(ws_base: str, ibom_id: str, lcsc: str) -> None:
    async with websockets.connect(f"{ws_base}/ws/scanner/{ibom_id}") as s:
        # initiale settings/presence abwarten
        try:
            await asyncio.wait_for(s.recv(), timeout=1.0)
        except asyncio.TimeoutError:
            pass
        await s.send(json.dumps({"type": "scan", "payload": "{pc:%s}" % lcsc}))
        await asyncio.sleep(0.6)


async def run(args) -> int:
    http_base = args.url.rstrip("/")
    ws_base = http_base.replace("http", "ws", 1)

    meta = upload(http_base, args.ibom)
    ibom_id = meta["id"]
    print(f"[i] Hochgeladen: {meta['name']}  (id={ibom_id}, {meta['distinct_lcsc']} Teile)")

    ok = True
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=args.channel, headless=True)
            ctx = await browser.new_context(ignore_https_errors=True)
            page = await ctx.new_page()
            await page.goto(f"{http_base}/ibom/{ibom_id}")

            # auf iBOM-Initialisierung + inject.js warten
            await page.wait_for_function(
                "typeof checkBomCheckbox === 'function' && typeof pcbdata !== 'undefined'",
                timeout=10000,
            )
            await page.wait_for_timeout(800)  # WS-Connect
            ready = await page.evaluate("typeof window.__LCSC_HELPER__ !== 'undefined'")
            print(f"[{'✓' if ready else '✗'}] iBOM initialisiert & Helper-Konfig vorhanden")
            ok = ok and ready

            sourced_before = await page.evaluate("readStorage('checkbox_Sourced') || ''")

            # Scan simulieren
            await send_scan(ws_base, ibom_id, args.lcsc)
            await page.wait_for_timeout(600)

            highlighted = await page.evaluate("document.querySelectorAll('.highlighted').length")
            rowid = await page.evaluate(
                "typeof currentHighlightedRowId !== 'undefined' ? currentHighlightedRowId : null")
            sourced_after = await page.evaluate("readStorage('checkbox_Sourced') || ''")
            toast = await page.evaluate(
                "(function(){var t=document.getElementById('__lcsc_toast');return t?t.textContent:''})()")
            row_checked = await page.evaluate(
                "(function(){var r=currentHighlightedRowId&&document.getElementById(currentHighlightedRowId);"
                "return r?r.querySelectorAll(\"input[type=checkbox]:checked\").length:0})()")

            await browser.close()

        def check(label, cond, detail=""):
            nonlocal ok
            ok = ok and cond
            print(f"[{'✓' if cond else '✗'}] {label}{('  — ' + detail) if detail else ''}")

        check("Zeile hervorgehoben", highlighted >= 1, f"{highlighted} .highlighted, rowid={rowid}")
        check("Erwartete Referenz im Toast", args.expect in toast, f"toast={toast!r}")
        check("Automatisch 'Sourced' abgehakt (localStorage)",
              sourced_after != sourced_before and sourced_after != "",
              f"vorher={sourced_before!r} nachher={sourced_after!r}")
        check("Checkbox der Zeile ist angehakt (DOM)", row_checked >= 1, f"{row_checked} checked")

    finally:
        delete(http_base, ibom_id)
        print("[i] Test-iBOM wieder entfernt.")

    print("\n" + ("✅ SMOKE-TEST BESTANDEN" if ok else "❌ SMOKE-TEST FEHLGESCHLAGEN"))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8077")
    ap.add_argument("--ibom", default=r"D:/Projects/FloraSense/electronics/Flora v0.4/ibom/ibom.html")
    ap.add_argument("--lcsc", default="C2906290")
    ap.add_argument("--expect", default="USB1")
    ap.add_argument("--channel", default="msedge", help="msedge | chrome | chromium")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
