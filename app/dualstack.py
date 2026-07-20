"""HTTP/HTTPS-Multiplexer auf einem einzigen Port.

Damit `http://host:PORT` **und** `https://host:PORT` funktionieren (Handys
ergaenzen bei nackter IP automatisch `http://`), lauscht ein kleiner TCP-Server
auf dem oeffentlichen Port und entscheidet anhand des ersten Bytes:

  * 0x16  → TLS-ClientHello  → roh an den internen HTTPS-Server (uvicorn)
            durchreichen (funktioniert auch fuer WebSockets, da nur die
            TLS-Bytes gespiegelt werden).
  * sonst → Klartext-HTTP    → mit 307-Redirect auf die https://-Adresse
            desselben Ports beantworten.

Der eigentliche uvicorn (mit TLS) laeuft nur auf 127.0.0.1 auf einem internen
Port; nach aussen ist ausschliesslich der Multiplexer sichtbar.
"""
from __future__ import annotations

import asyncio
import socket


async def _pipe(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await src.read(65536)
            if not data:
                break
            dst.write(data)
            await dst.drain()
    except Exception:
        pass
    finally:
        try:
            dst.close()
        except Exception:
            pass


def _parse_http(text: str) -> tuple[str, str]:
    """Liefert (host_ohne_port, pfad_mit_query) aus einer HTTP-Anfrage."""
    lines = text.split("\r\n")
    path = "/"
    if lines and " " in lines[0]:
        parts = lines[0].split(" ")
        if len(parts) >= 2 and parts[1].startswith("/"):
            path = parts[1]
    host = ""
    for ln in lines[1:]:
        if ln.lower().startswith("host:"):
            host = ln.split(":", 1)[1].strip().split(":")[0]
            break
    return host, path


async def _handle(client_r, client_w, internal_port, public_port):
    try:
        first = await client_r.read(1)
        if not first:
            client_w.close()
            return

        if first == b"\x16":  # TLS ClientHello -> an internen HTTPS-Server proxyen
            server_r = server_w = None
            for _ in range(60):  # warten, bis der interne Server bereit ist
                try:
                    server_r, server_w = await asyncio.open_connection("127.0.0.1", internal_port)
                    break
                except OSError:
                    await asyncio.sleep(0.1)
            if server_w is None:
                client_w.close()
                return
            server_w.write(first)
            await server_w.drain()
            await asyncio.gather(
                _pipe(client_r, server_w),
                _pipe(server_r, client_w),
            )
        else:  # Klartext-HTTP -> 307 auf https:// desselben Ports
            rest = await client_r.read(8192)
            text = (first + rest).decode("latin1", "ignore")
            host, path = _parse_http(text)
            netloc = f"{host}:{public_port}" if host else f"localhost:{public_port}"
            location = f"https://{netloc}{path}"
            resp = (
                "HTTP/1.1 307 Temporary Redirect\r\n"
                f"Location: {location}\r\n"
                "Content-Length: 0\r\n"
                "Connection: close\r\n\r\n"
            )
            client_w.write(resp.encode("latin1"))
            await client_w.drain()
            client_w.close()
    except Exception:
        try:
            client_w.close()
        except Exception:
            pass


def _free_local_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def serve_dual(app, host: str, public_port: int, cert_path, key_path) -> None:
    """Startet internen HTTPS-uvicorn + Multiplexer im selben Event-Loop."""
    import uvicorn

    internal_port = _free_local_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=internal_port,
        ssl_certfile=str(cert_path),
        ssl_keyfile=str(key_path),
        log_level="info",
    )
    server = uvicorn.Server(config)

    mux = await asyncio.start_server(
        lambda r, w: _handle(r, w, internal_port, public_port),
        host,
        public_port,
    )
    await asyncio.gather(server.serve(), mux.serve_forever())
