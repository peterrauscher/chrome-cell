from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse, urlunparse

from aiohttp import ClientSession, ClientWSTimeout, WSMsgType, web

NUM_CELLS = int(os.environ.get("NUM_CELLS", "5"))
CELL_HOST_TEMPLATE = os.environ.get(
    "CELL_HOST_TEMPLATE",
    "chrome-cell-{id}.chrome-cell.chrome-cell.svc.cluster.local",
)
CELL_CDP_PORT = int(os.environ.get("CELL_CDP_PORT", "9223"))


def cell_host(cell_id: int) -> str:
    return CELL_HOST_TEMPLATE.format(id=cell_id)


def rewrite_ws(url: str, host: str, port: int) -> str:
    parsed = urlparse(url)
    return urlunparse(("ws", f"{host}:{port}", parsed.path, "", parsed.query, ""))


@dataclass
class CellState:
    cell_id: int
    lock: str = "agent"
    lock_holder: str = ""
    lock_at: float = 0.0
    cdp: Any = None
    cdp_task: asyncio.Task | None = None
    clients: set[web.WebSocketResponse] = field(default_factory=set)
    next_id: int = 1
    last_meta: dict | None = None

    def snapshot(self) -> dict:
        return {
            "id": self.cell_id,
            "host": cell_host(self.cell_id),
            "lock": self.lock,
            "lock_holder": self.lock_holder,
            "viewers": len(self.clients),
            "cast": self.cdp is not None and not self.cdp.closed,
        }


class Hub:
    def __init__(self, session: ClientSession):
        self.session = session
        self.cells = {i: CellState(i) for i in range(NUM_CELLS)}
        self._locks = {i: asyncio.Lock() for i in range(NUM_CELLS)}

    async def attach(self, cell_id: int, ws: web.WebSocketResponse) -> CellState:
        state = self.cells[cell_id]
        async with self._locks[cell_id]:
            state.clients.add(ws)
            if state.cdp is None or state.cdp.closed:
                await self._connect_cdp(state)
        return state

    async def detach(self, cell_id: int, ws: web.WebSocketResponse) -> None:
        state = self.cells[cell_id]
        async with self._locks[cell_id]:
            state.clients.discard(ws)
            if not state.clients and state.cdp is not None:
                await self._close_cdp(state)

    async def _connect_cdp(self, state: CellState) -> None:
        host = cell_host(state.cell_id)
        base = f"http://{host}:{CELL_CDP_PORT}"
        targets = []
        try:
            async with self.session.get(f"{base}/json/list", timeout=5) as resp:
                if resp.status == 200:
                    targets = await resp.json()
        except Exception as exc:
            raise web.HTTPBadGateway(text=f"cdp list failed: {exc}") from exc

        page = next((t for t in targets if t.get("type") == "page"), None)
        if page is None:
            async with self.session.put(f"{base}/json/new?about:blank") as resp:
                page = await resp.json()

        ws_url = rewrite_ws(page["webSocketDebuggerUrl"], host, CELL_CDP_PORT)
        state.cdp = await self.session.ws_connect(
            ws_url, timeout=ClientWSTimeout(ws_close=10)
        )
        state.next_id = 1
        await self._cdp_send(state, "Page.enable")
        await self._cdp_send(
            state,
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 55,
                "maxWidth": 1280,
                "maxHeight": 720,
                "everyNthFrame": 1,
            },
        )
        state.cdp_task = asyncio.create_task(self._pump(state))

    async def _cdp_send(self, state: CellState, method: str, params: dict | None = None) -> int:
        msg_id = state.next_id
        state.next_id += 1
        payload: dict[str, Any] = {"id": msg_id, "method": method}
        if params:
            payload["params"] = params
        assert state.cdp is not None
        await state.cdp.send_str(json.dumps(payload))
        return msg_id

    async def _pump(self, state: CellState) -> None:
        assert state.cdp is not None
        try:
            async for msg in state.cdp:
                if msg.type != WSMsgType.TEXT:
                    if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
                    continue
                data = json.loads(msg.data)
                if data.get("method") != "Page.screencastFrame":
                    continue
                params = data["params"]
                state.last_meta = params.get("metadata")
                raw = json.dumps(
                    {
                        "type": "frame",
                        "data": params["data"],
                        "metadata": params.get("metadata"),
                        "lock": state.lock,
                    }
                )
                dead = []
                for client in list(state.clients):
                    try:
                        await client.send_str(raw)
                    except Exception:
                        dead.append(client)
                for client in dead:
                    state.clients.discard(client)
                try:
                    await self._cdp_send(
                        state, "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
                    )
                except Exception:
                    break
        except Exception:
            pass
        finally:
            await self._close_cdp(state)

    async def _close_cdp(self, state: CellState) -> None:
        if state.cdp is not None and not state.cdp.closed:
            try:
                await state.cdp.close()
            except Exception:
                pass
        state.cdp = None
        task = state.cdp_task
        state.cdp_task = None
        if task and task is not asyncio.current_task():
            task.cancel()

    async def handle_input(self, state: CellState, event: dict) -> None:
        if state.lock != "human" or state.cdp is None:
            return
        kind = event.get("kind")
        if kind == "mouse":
            params = {
                "type": event["event"],
                "x": float(event["x"]),
                "y": float(event["y"]),
                "button": event.get("button", "none"),
                "clickCount": int(event.get("clickCount", 0)),
                "modifiers": int(event.get("modifiers", 0)),
            }
            if event["event"] == "mouseWheel":
                params["deltaX"] = float(event.get("deltaX", 0))
                params["deltaY"] = float(event.get("deltaY", 0))
            await self._cdp_send(state, "Input.dispatchMouseEvent", params)
        elif kind == "key":
            await self._cdp_send(
                state,
                "Input.dispatchKeyEvent",
                {
                    "type": event["event"],
                    "key": event.get("key", ""),
                    "code": event.get("code", ""),
                    "text": event.get("text", ""),
                    "unmodifiedText": event.get("text", ""),
                    "modifiers": int(event.get("modifiers", 0)),
                    "nativeVirtualKeyCode": int(event.get("vk", 0)),
                    "windowsVirtualKeyCode": int(event.get("vk", 0)),
                },
            )
