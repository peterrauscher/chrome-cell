"""Viewport gateway for chrome-cell."""

from __future__ import annotations

import asyncio
import json
import os
import time

from aiohttp import ClientSession, WSMsgType, web

from hub import NUM_CELLS, Hub, cell_host

CELL_VNC_PORT = int(os.environ.get("CELL_VNC_PORT", "3000"))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
TOKEN = os.environ.get("VIEWPORT_TOKEN", "")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
}


def valid_id(raw: str) -> int:
    cell_id = int(raw)
    if cell_id < 0 or cell_id >= NUM_CELLS:
        raise web.HTTPNotFound(text=f"cell {cell_id} out of range")
    return cell_id


def check_token(request: web.Request) -> None:
    if not TOKEN:
        return
    got = request.headers.get("Authorization", "")
    if got == f"Bearer {TOKEN}" or request.query.get("token") == TOKEN:
        return
    raise web.HTTPUnauthorized(text="bad token")


def filter_req_headers(headers) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() not in HOP}


async def api_cells(request: web.Request) -> web.Response:
    check_token(request)
    hub: Hub = request.app["hub"]
    return web.json_response([hub.cells[i].snapshot() for i in range(NUM_CELLS)])


async def api_cell(request: web.Request) -> web.Response:
    check_token(request)
    hub: Hub = request.app["hub"]
    return web.json_response(hub.cells[valid_id(request.match_info["id"])].snapshot())


async def api_lock(request: web.Request) -> web.Response:
    check_token(request)
    state = request.app["hub"].cells[valid_id(request.match_info["id"])]
    body = {}
    if request.can_read_body and request.content_type == "application/json":
        body = await request.json()
    state.lock = "human"
    state.lock_holder = str(body.get("holder") or request.query.get("holder") or "user")
    state.lock_at = time.time()
    return web.json_response(state.snapshot())


async def api_unlock(request: web.Request) -> web.Response:
    check_token(request)
    state = request.app["hub"].cells[valid_id(request.match_info["id"])]
    state.lock = "agent"
    state.lock_holder = ""
    return web.json_response(state.snapshot())


async def cast_ws(request: web.Request) -> web.WebSocketResponse:
    check_token(request)
    hub: Hub = request.app["hub"]
    cell_id = valid_id(request.match_info["id"])
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    try:
        state = await hub.attach(cell_id, ws)
    except web.HTTPException as exc:
        await ws.send_str(json.dumps({"type": "error", "error": exc.text}))
        await ws.close()
        return ws
    await ws.send_str(json.dumps({"type": "hello", **state.snapshot()}))
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                break
            event = json.loads(msg.data)
            etype = event.get("type")
            if etype == "lock":
                state.lock = "human"
                state.lock_holder = str(event.get("holder") or "user")
                await ws.send_str(json.dumps({"type": "hello", **state.snapshot()}))
            elif etype == "unlock":
                state.lock = "agent"
                state.lock_holder = ""
                await ws.send_str(json.dumps({"type": "hello", **state.snapshot()}))
            elif etype == "input":
                await hub.handle_input(state, event)
    finally:
        await hub.detach(cell_id, ws)
    return ws


async def proxy_vnc(request: web.Request) -> web.StreamResponse:
    check_token(request)
    cell_id = valid_id(request.match_info["id"])
    rest = request.match_info.get("rest", "")
    host = cell_host(cell_id)
    target = f"http://{host}:{CELL_VNC_PORT}/{rest}"
    if request.query_string:
        target += f"?{request.query_string}"
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _proxy_ws(request, target.replace("http://", "ws://", 1))
    session: ClientSession = request.app["session"]
    try:
        async with session.request(
            request.method, target,
            headers=filter_req_headers(request.headers),
            data=await request.read() or None,
            allow_redirects=False,
        ) as resp:
            out = web.StreamResponse(status=resp.status, reason=resp.reason)
            for k, v in resp.headers.items():
                if k.lower() not in HOP and k.lower() != "content-encoding":
                    out.headers[k] = v
            await out.prepare(request)
            async for chunk in resp.content.iter_chunked(64 * 1024):
                await out.write(chunk)
            await out.write_eof()
            return out
    except Exception as exc:
        raise web.HTTPBadGateway(text=f"vnc proxy: {exc}") from exc


async def _proxy_ws(request: web.Request, target: str) -> web.WebSocketResponse:
    session: ClientSession = request.app["session"]
    client = web.WebSocketResponse(protocols=request.headers.getall("Sec-WebSocket-Protocol", ()))
    await client.prepare(request)
    upstream = await session.ws_connect(
        target,
        headers=filter_req_headers(request.headers),
        protocols=[client.ws_protocol] if client.ws_protocol else None,
    )

    async def c2u():
        async for msg in client:
            if msg.type == WSMsgType.TEXT:
                await upstream.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await upstream.send_bytes(msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break

    async def u2c():
        async for msg in upstream:
            if msg.type == WSMsgType.TEXT:
                await client.send_str(msg.data)
            elif msg.type == WSMsgType.BINARY:
                await client.send_bytes(msg.data)
            elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                break

    try:
        await asyncio.gather(c2u(), u2c())
    except Exception:
        pass
    finally:
        if not upstream.closed:
            await upstream.close()
    return client


async def index(_request: web.Request) -> web.FileResponse:
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def health(_request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "cells": NUM_CELLS})


async def on_startup(app: web.Application) -> None:
    session = ClientSession()
    app["session"] = session
    app["hub"] = Hub(session)


async def on_cleanup(app: web.Application) -> None:
    hub: Hub = app["hub"]
    for state in hub.cells.values():
        await hub._close_cdp(state)
    await app["session"].close()


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/healthz", health)
    app.router.add_get("/api/cells", api_cells)
    app.router.add_get("/api/cells/{id}", api_cell)
    app.router.add_post("/api/cells/{id}/lock", api_lock)
    app.router.add_delete("/api/cells/{id}/lock", api_unlock)
    app.router.add_get("/cast/{id}", cast_ws)
    app.router.add_route("*", "/cells/{id}/", proxy_vnc)
    app.router.add_route("*", "/cells/{id}/{rest:.*}", proxy_vnc)
    app.router.add_static("/static", STATIC_DIR)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), host=LISTEN_HOST, port=LISTEN_PORT)
