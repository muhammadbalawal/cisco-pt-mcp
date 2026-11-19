"""Socket.IO bridge to the Packet Tracer plugin.

Listens on ``127.0.0.1:7531`` for the headless cisco-pt-mcp plugin to
connect. Translates ``call_tool(name, args)`` into a ``tool_call`` event,
awaits the matching ``tool_result`` (correlated by ``tool_call_id``), and
returns the raw result dict to the MCP server.

Wire protocol:

* server -> plugin
    - ``tool_call``  ``{tool_call_id, tool_name, tool_input}``

* plugin -> server
    - ``tool_result``  ``{tool_call_id, tool_name, tool_input, result}``

Loopback only. If a second plugin connects, the previous sid is
disconnected so there is exactly one live PT at any time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import Mapping
from typing import Any

import socketio
from aiohttp import web

log = logging.getLogger(__name__)


class PTBridgeProtocolError(RuntimeError):
    """Raised when the Packet Tracer plugin violates the bridge protocol."""


# Loopback only — both ends are local.
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 7531

# Bumped via env when long IOS command bursts exceed the default.
TOOL_TIMEOUT_S = float(os.environ.get("CISCO_PT_MCP_TOOL_TIMEOUT", "60"))


class PTBridge:
    """Async Socket.IO server that proxies MCP tool calls to the PT plugin."""

    def __init__(
        self,
        host: str = BRIDGE_HOST,
        port: int = BRIDGE_PORT,
        tool_timeout: float = TOOL_TIMEOUT_S,
    ) -> None:
        self.host = host
        self.port = port
        self.tool_timeout = tool_timeout

        # cors_allowed_origins="*" is safe — the listener is bound to loopback.
        self._sio = socketio.AsyncServer(
            async_mode="aiohttp",
            cors_allowed_origins="*",
            logger=False,
            engineio_logger=False,
        )
        self._app = web.Application()
        self._sio.attach(self._app)

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        # Single-plugin model. If a second plugin connects, kick the first.
        self._sid: str | None = None
        self._connected = asyncio.Event()
        self._pending: dict[str, asyncio.Future[dict]] = {}

        self._register_handlers()

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        log.info("PT bridge listening on http://%s:%d", self.host, self.port)

    async def stop(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(RuntimeError("bridge stopped"))
        self._pending.clear()
        if self._site is not None:
            await self._site.stop()
            self._site = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    def _register_handlers(self) -> None:
        sio = self._sio

        @sio.event
        async def connect(sid: str, environ: dict, auth: Any = None) -> None:
            log.info("PT plugin connected sid=%s", sid)
            old_sid = self._sid
            self._sid = sid
            self._connected.set()
            if old_sid is not None and old_sid != sid:
                # A previous plugin is still attached. Drop it so we don't
                # silently route traffic to a stale sid.
                log.info("disconnecting older PT plugin sid=%s", old_sid)
                try:
                    await sio.disconnect(old_sid)
                except Exception:  # noqa: BLE001
                    pass

        @sio.event
        async def disconnect(sid: str) -> None:
            log.info("PT plugin disconnected sid=%s", sid)
            if self._sid == sid:
                self._sid = None
                self._connected.clear()
                # Fail outstanding tool calls — the plugin can't answer them.
                for tcid, fut in list(self._pending.items()):
                    if not fut.done():
                        fut.set_exception(
                            RuntimeError("PT plugin disconnected mid-call")
                        )
                    self._pending.pop(tcid, None)

        @sio.on("tool_result")
        async def on_tool_result(_sid: str, data: dict[str, Any] | None) -> None:
            if not isinstance(data, Mapping):
                log.warning("tool_result must be an object: %r", data)
                return

            tcid = data.get("tool_call_id")
            if not isinstance(tcid, str) or not tcid:
                log.warning("tool_result missing tool_call_id: %r", data)
                return

            fut = self._pending.pop(tcid, None)
            if fut is None or fut.done():
                return

            if data.get("tool_name") is not None and not isinstance(data.get("tool_name"), str):
                fut.set_exception(PTBridgeProtocolError("tool_result field 'tool_name' must be a string when present"))
                return

            tool_input = data.get("tool_input")
            if tool_input is not None and not isinstance(tool_input, Mapping):
                fut.set_exception(PTBridgeProtocolError("tool_result field 'tool_input' must be an object when present"))
                return

            if "result" not in data:
                fut.set_exception(PTBridgeProtocolError("tool_result missing required field 'result'"))
                return

            result = data.get("result")

            # PT's $se bridge can deliver the runCode return as a JSON
            # string (sometimes double-encoded) instead of an object —
            # peel string layers until a real value comes out.
            for _ in range(4):
                if not isinstance(result, str):
                    break
                try:
                    result = json.loads(result)
                except (json.JSONDecodeError, TypeError):
                    break

            # An un-unwrapped runCode envelope {success, result, code} —
            # collapse it to the userfunctions payload.
            if isinstance(result, dict) and {"success", "result", "code"} <= set(result):
                result = result["result"]

            if not isinstance(result, Mapping):
                fut.set_exception(PTBridgeProtocolError("tool_result field 'result' must be an object"))
                return

            fut.set_result(dict(result))

    @property
    def is_connected(self) -> bool:
        return self._sid is not None

    async def wait_until_connected(self, timeout: float | None = None) -> None:
        if self.is_connected:
            return
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"Packet Tracer plugin did not connect within {timeout:.0f}s. "
                "Open Packet Tracer with the cisco-pt-mcp bridge plugin loaded."
            ) from exc

    async def call_tool(self, tool_name: str, tool_input: dict[str, Any]) -> dict:
        """Send tool_call, await tool_result, return its ``result`` payload."""
        if self._sid is None:
            raise RuntimeError(
                "No Packet Tracer plugin connected. Open Packet Tracer with "
                "the cisco-pt-mcp bridge plugin loaded."
            )

        tcid = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict] = loop.create_future()
        self._pending[tcid] = fut

        await self._sio.emit(
            "tool_call",
            {"tool_call_id": tcid, "tool_name": tool_name, "tool_input": tool_input},
            to=self._sid,
        )

        try:
            return await asyncio.wait_for(fut, timeout=self.tool_timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(tcid, None)
            raise RuntimeError(
                f"Tool '{tool_name}' timed out after {self.tool_timeout:.0f}s "
                f"waiting for the Packet Tracer plugin"
            ) from exc
