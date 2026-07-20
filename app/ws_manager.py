"""WebSocket-Verwaltung: pro iBOM ein 'Raum' mit Viewer- und Scanner-Verbindungen.

Der PC oeffnet eine iBOM (Rolle ``viewer``), das Smartphone verbindet sich zur
selben iBOM (Rolle ``scanner``) und schickt gescannte Codes in den Raum.
"""
from __future__ import annotations

from fastapi import WebSocket


class WSManager:
    def __init__(self) -> None:
        # ibom_id -> {"viewer": set[WebSocket], "scanner": set[WebSocket]}
        self._rooms: dict[str, dict[str, set[WebSocket]]] = {}

    def _room(self, ibom_id: str) -> dict[str, set[WebSocket]]:
        return self._rooms.setdefault(ibom_id, {"viewer": set(), "scanner": set()})

    async def connect(self, ws: WebSocket, ibom_id: str, role: str) -> None:
        await ws.accept()
        self._room(ibom_id)[role].add(ws)
        # beiden Seiten die aktuelle Belegung mitteilen
        await self.broadcast_presence(ibom_id)

    def disconnect(self, ws: WebSocket, ibom_id: str, role: str) -> None:
        room = self._rooms.get(ibom_id)
        if not room:
            return
        room[role].discard(ws)
        if not room["viewer"] and not room["scanner"]:
            self._rooms.pop(ibom_id, None)

    def counts(self, ibom_id: str) -> dict:
        room = self._rooms.get(ibom_id, {"viewer": set(), "scanner": set()})
        return {"viewers": len(room["viewer"]), "scanners": len(room["scanner"])}

    def active_rooms(self) -> list[str]:
        return list(self._rooms.keys())

    async def _send_many(self, targets: set[WebSocket], message: dict) -> None:
        dead = []
        for ws in list(targets):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            targets.discard(ws)

    async def send_to_role(self, ibom_id: str, role: str, message: dict) -> None:
        room = self._rooms.get(ibom_id)
        if room:
            await self._send_many(room[role], message)

    async def broadcast_presence(self, ibom_id: str) -> None:
        counts = self.counts(ibom_id)
        msg = {"type": "presence", **counts}
        await self.send_to_role(ibom_id, "viewer", msg)
        await self.send_to_role(ibom_id, "scanner", msg)

    async def broadcast_all(self, message: dict) -> None:
        """Sendet an alle Verbindungen aller Raeume (z.B. geaenderte Einstellungen)."""
        for room in list(self._rooms.values()):
            await self._send_many(room["viewer"], message)
            await self._send_many(room["scanner"], message)


manager = WSManager()
