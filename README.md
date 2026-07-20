# 📦 scan2place

**Scan the QR code on your LCSC/JLCPCB part packaging with your phone — the matching
component lights up in your interactive BOM on the PC and gets checked off. Instantly.**

No searching. No comparing part numbers by hand. Just scan, source, place — done.

* **PC (Viewer):** shows the real KiCad *InteractiveHtmlBom*. Scanned parts are
  highlighted, scrolled into view and ticked off.
* **Phone (Scanner):** scans the QR/DataMatrix labels on the part packaging and sends
  them live to the Viewer over your LAN.
* Works with **any** iBOM from
  [openscopeproject/InteractiveHtmlBom](https://github.com/openscopeproject/InteractiveHtmlBom) —
  the LCSC part field is detected automatically (by name *or* value pattern).
* Multiple iBOMs, progress **stored on the server** per BOM — pick up where you left off.

> 📖 A longer, illustrated write-up with architecture and data-flow diagrams lives in
> [`docs/README.md`](docs/README.md).

---

## Getting started (self-hosting)

The container image is published to the GitHub Container Registry, so a minimal
`docker-compose.yml` is all you need — no build step:

```yaml
services:
  scan2place:
    image: ghcr.io/theautomatist/scan2place:latest
    ports:
      - "8090:8090"
    volumes:
      - ./data:/app/data      # persistent: iBOMs, progress, LCSC cache, TLS cert
    environment:
      - USE_HTTPS=1           # self-signed TLS — required for the phone camera
    restart: unless-stopped
```

```bash
docker compose up -d
```

The app is now reachable on your LAN at **`https://<PC-IP>:8090`** (find the IP with
`ipconfig` / `ip addr`). You can type the bare address — `http://` is redirected to
`https://` automatically.

> **HTTPS / certificate:** on first start the server generates a self-signed certificate
> under `data/certs/`. This is **required so the phone's browser grants camera access**
> (browsers only allow the camera over HTTPS). Accept the one-time browser warning
> (*Advanced → Continue*). The server serves both `http://` (→ redirect) and `https://`
> on the **same** port (8090).

### Behind a reverse proxy

Running behind Traefik / Caddy / nginx-proxy-manager that already terminates TLS? Set
`USE_HTTPS=0` so the app speaks plain HTTP and lets the proxy handle certificates. The
proxy **must** serve `https://`, otherwise the phone gets no camera access.

### Build from source instead

```bash
git clone https://github.com/theautomatist/scan2place.git
cd scan2place
docker compose up -d --build
```

The bundled [`docker-compose.yml`](docker-compose.yml) carries both `image:` and
`build:`, so `--build` compiles locally while a plain `up -d` pulls the published image.
Change the host port in `ports:` if `8090` is taken.

---

## How you use it

1. **On the PC**, open `https://<PC-IP>:8090`, **upload** your `ibom.html` from the
   sidebar → it opens as the **Viewer**.
2. Click **Connect scanner** (top right) → a **QR code** appears.
3. **Scan that QR** with your **phone** → the Scanner opens for exactly this BOM.
4. Scan the part packaging → the component is highlighted and ticked off in the Viewer;
   the phone shows a green confirmation with reference(s) and value.

### The sourcing → placing pipeline

scan2place guides you through two phases, tracked per BOM on the server:

1. **① Sourcing** — every scan marks that position as **Sourced**. Once *all* positions
   are sourced, the phase advances to *Placing* automatically.
2. **② Placing** — a scan asks the phone *"all N parts placed?"*; after you confirm, the
   position is marked **Placed**.

The Viewer shows a phase switcher (also switchable by hand) and a dual progress bar
(`Sourced 12/30 · Placed 5/30`). Placed rows are tinted a subtle green.

### QR-code format

JLCPCB/LCSC packaging labels carry a QR code like:

```
{pbn:PICK2607040232,on:WM2607040155,pc:C2906290,pm:TYPE-C 16P CB1.6 073,qty:15,...}
```

The **`pc`** field is the LCSC part number (`C2906290`) that gets matched against the
BOM. Bare LCSC numbers (`C2906290`) are recognised too. A single QR that maps to several
components (e.g. 5× the same capacitor) highlights and ticks off the whole group.

---

## Alternative parts

Sometimes a **functionally-equivalent part with a different LCSC number** is fitted
(e.g. a 470nF/0402 from another manufacturer). The scanner handles it:

1. If the scanned LCSC number is **not** in the BOM, it is **looked up online at LCSC**
   (value, package, part type) — results are cached in `data/lcsc_cache/`.
2. If a BOM part matches by **type + value + package**, the camera **pauses** and shows
   two large one-handed buttons: **[✓ Adopt]** and **[✗ Reject]**. The camera resumes
   only after you decide.
3. On *Adopt*, the original BOM row is **greyed out** and a **cloned row with the
   alternative's real data** (fetched from the LCSC API) is inserted right below it.
   Hovering that alt row highlights its footprints on the PCB. It can be removed again
   via the small ✕ on the right. The position counts as *Sourced*.
4. If nothing matches, the phone reports *"not part of this project"*.

Highlighting of alt rows (on/off + colour) is configurable under **⚙ Settings**
(default: light blue). The online lookup needs an internet connection.

---

## Settings

Under **⚙ Settings** (bottom left):

| Setting | Effect |
|---|---|
| **Scroll to component** | Viewer jumps to the scanned BOM row |
| **Highlight alternatives** + colour | Tint rows that have an adopted alternative |
| **Highlight placed rows** | Subtle green tint on placed rows |
| **Sound** / **Vibrate** | Feedback on the phone on a successful scan |

Progress (which positions are *Sourced* / *Placed*) is stored per iBOM and survives
restarts — reopen the same BOM to continue another day.

---

## Local development (without Docker)

```bash
pip install -r requirements.txt

# With HTTPS (default, for the phone camera):
python -m app.main

# Without HTTPS (e.g. PC-only / testing):
USE_HTTPS=0 python -m app.main      # PowerShell:  $env:USE_HTTPS=0; python -m app.main
```

Environment variables: `PORT` (default `8090`), `HOST` (default `0.0.0.0`),
`USE_HTTPS` (`1`/`0`).

### End-to-end smoke test (no browser extension)

Verifies headlessly with a real browser (Edge/Chrome) that a simulated scan actually
highlights and ticks off the component in the iBOM:

```bash
pip install playwright websockets

# Terminal 1 — start the server:
USE_HTTPS=0 PORT=8077 python -m app.main

# Terminal 2 — run the test:
python tests/browser_smoke.py --url http://127.0.0.1:8077 \
    --ibom "path/to/your/ibom.html"
```

Pick the browser channel with `--channel msedge|chrome|chromium` (default `msedge`).

---

## How it works

* The server reads the iBOM (`pcbdata` is LZString-compressed), detects the LCSC field
  and builds an index **LCSC number → references / footprints**.
* When serving the iBOM (inside the Viewer's `<iframe>`), a small **sync script**
  (`static/inject.js`) is injected. It drives the BOM's own functions
  (`checkBomCheckbox`, `footprintIndexToHandler`, `readStorage`/`writeStorage`,
  `EventHandler`) to highlight rows, tick checkboxes and report changes to the server.
* A **WebSocket** connects Scanner (phone) and Viewer (PC) in the same "room" (= iBOM).
* The original `ibom.html` is never modified — all state lives separately under `data/`.

---

## Project structure

```
app/
  main.py            FastAPI: routes, iBOM serving, WebSocket, HTTPS start
  ibom_processor.py  parse pcbdata, detect LCSC field, index, alternatives, progress, inject
  lcsc_api.py        online LCSC part lookup (+ disk cache)
  values.py          value / package normalisation for matching
  qr.py              LCSC/JLCPCB QR payload parser
  storage.py         file-based persistence (iBOMs, state, alternatives, settings)
  ws_manager.py      WebSocket rooms (viewer / scanner)
  dualstack.py       single-port http→https multiplexer
  certs.py           self-signed TLS certificate
static/
  app.js  scanner.js  inject.js  style.css
  vendor/            html5-qrcode (scanner) · qrcode-generator (pairing QR)
templates/index.html
tests/               headless end-to-end smoke tests (Playwright, no extension needed)
data/                (runtime) uploaded iBOMs, state, cache, certificate
.github/workflows/   CI: build & publish the Docker image to ghcr.io
```

---

## Troubleshooting

* **Camera won't start on the phone** → the page must be opened over **HTTPS**
  (`https://…`) and camera access granted. Accept the certificate warning once.
* **"no viewer open"** on the scanner → the same iBOM must be open in the Viewer on the PC.
* **Part not found** → check the iBOM actually has an LCSC field (the ⚠ in the list warns
  otherwise) and that the scanned `pc` number matches it.
* **Port 8090 in use** → change it in `docker-compose.yml` (`ports:` **and** `PORT=`).
* **`docker compose up -d` can't pull the image** → the package may be private; either
  make it public in the repo's *Packages* settings, or `docker login ghcr.io` first.
