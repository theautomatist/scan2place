"""End-to-End-Test des Alternative-Features (headless, ohne Browser-Extension).

Simuliert: Scan einer nicht in der BOM enthaltenen LCSC-Nummer -> Online-Lookup
-> Alternative-Vorschlag -> Uebernahme. Prueft im echten iBOM-DOM, dass die
Spalte "Alt. LCSC" erscheint, die Ziel-Zeile die Alternative zeigt und abgehakt
wird.

Voraussetzung: Server laeuft, Internet verfuegbar (LCSC-API).
    USE_HTTPS=0 PORT=8096 python -m app.main
    python tests/browser_alt_smoke.py --url http://127.0.0.1:8096 --lcsc C88946
"""
from __future__ import annotations

import argparse
import asyncio
import json
import urllib.request
import uuid

import websockets
from playwright.async_api import async_playwright


def _http(method, url, body=None, headers=None):
    req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def upload(http, path):
    b = uuid.uuid4().hex
    data = open(path, "rb").read()
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"ibom.html\""
            f"\r\nContent-Type: text/html\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return _http("POST", f"{http}/api/iboms", body,
                 {"Content-Type": f"multipart/form-data; boundary={b}"})


async def run(args):
    http = args.url.rstrip("/")
    ws_base = http.replace("http", "ws", 1)
    meta = upload(http, args.ibom)
    iid = meta["id"]
    print(f"[i] Hochgeladen: {meta['name']} (id={iid})")

    ok = True
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(channel=args.channel, headless=True)
            page = await (await browser.new_context(ignore_https_errors=True)).new_page()
            await page.goto(f"{http}/ibom/{iid}")
            await page.wait_for_function(
                "typeof checkBomCheckbox==='function' && typeof pcbdata!=='undefined'", timeout=10000)
            await page.wait_for_timeout(900)

            has_col = await page.evaluate("config.fields.indexOf('Alt. LCSC') !== -1")
            print(f"[{'✓' if has_col else '✗'}] Spalte 'Alt. LCSC' in die BOM eingefuegt")
            ok = ok and has_col

            # Scan + Uebernahme ueber WebSocket
            async with websockets.connect(f"{ws_base}/ws/scanner/{iid}") as sc:
                await asyncio.sleep(0.4)
                await sc.send(json.dumps({"type": "scan", "payload": "{pc:%s}" % args.lcsc}))
                cand = None
                for _ in range(20):
                    m = json.loads(await asyncio.wait_for(sc.recv(), 6))
                    if m.get("type") == "scan_result":
                        print(f"[i] Scan {args.lcsc}: matched={m['matched']}")
                        if m.get("candidates"):
                            cand = m["candidates"][0]
                        break
                if not cand:
                    print("[✗] Kein Alternative-Kandidat erhalten"); await browser.close(); return 1
                print(f"[i] Kandidat: {cand['value']} @ {','.join(cand['refs'])}")
                await sc.send(json.dumps({
                    "type": "set_alternative", "altLcsc": args.lcsc, "altMpn": "TEST",
                    "altValue": cand["value"], "altPackage": cand.get("size"),
                    "targetLcsc": cand["original_lcsc"], "refs": cand["refs"],
                    "footprints": cand["footprints"],
                }))
                await asyncio.sleep(1.0)

            await page.wait_for_timeout(600)
            fp = cand["footprints"][0]
            cell = await page.evaluate(
                "(function(fp){var i=config.fields.indexOf('Alt. LCSC');"
                "var r=pcbdata.bom.fields[fp]||pcbdata.bom.fields[String(fp)];"
                "return r?r[i]:null})(%d)" % fp)
            sourced = await page.evaluate("readStorage('checkbox_Sourced')||''")
            dom_has = await page.evaluate("document.body.innerText.indexOf('%s')>=0" % args.lcsc)
            await browser.close()

            def chk(label, cond, detail=""):
                nonlocal ok; ok = ok and cond
                print(f"[{'✓' if cond else '✗'}] {label}{('  — ' + detail) if detail else ''}")

            chk("Alternative in Zielzeile (bom.fields)", cell == args.lcsc, f"Zelle={cell!r}")
            chk("Alternative im gerenderten DOM sichtbar", dom_has, "")
            chk("Zielgruppe automatisch als 'Sourced' abgehakt",
                str(fp) in (sourced.split(",") if sourced else []), f"Sourced={sourced!r}")
    finally:
        _http("DELETE", f"{http}/api/iboms/{iid}")
        print("[i] Test-iBOM entfernt.")

    print("\n" + ("✅ ALT-SMOKE-TEST BESTANDEN" if ok else "❌ FEHLGESCHLAGEN"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8096")
    ap.add_argument("--ibom", default=r"D:/Projects/FloraSense/electronics/Flora v0.4/ibom/ibom.html")
    ap.add_argument("--lcsc", default="C88946")
    ap.add_argument("--channel", default="msedge")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
