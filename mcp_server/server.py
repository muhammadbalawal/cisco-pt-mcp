"""MCP server (stdio) that registers the Packet Tracer tool set and forwards
calls through ``PTBridge`` to the PT extension."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

import mcp.types as mcp_types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from .bridge import PTBridge
from .lifecycle import orphan_watchdog
from .pc import run_pc_command
from .slots import call_add_module
from .tools import TOOLS, TOOLS_BY_NAME

log = logging.getLogger(__name__)


class MCPRequestError(ValueError):
    """Raised when an MCP client sends malformed tool arguments."""


def _to_text_content(payload: Any) -> list[mcp_types.TextContent]:
    """Wrap a JSON-serializable payload as a single TextContent block."""
    if isinstance(payload, str):
        text = payload
    else:
        try:
            text = json.dumps(payload, indent=2, default=str)
        except (TypeError, ValueError):
            text = str(payload)
    return [mcp_types.TextContent(type="text", text=text)]


def _normalize_tool_arguments(arguments: Any) -> dict[str, Any]:
    """Ensure MCP tool arguments arrive as a JSON object."""
    if arguments is None:
        return {}
    if not isinstance(arguments, dict):
        raise MCPRequestError("tool arguments must be a JSON object")
    return arguments


def build_server(bridge: PTBridge) -> Server:
    """Wire MCP handlers onto a fresh ``Server`` instance."""
    app: Server = Server("cisco-pt-mcp")
    descriptors = [
        mcp_types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["inputSchema"],
        )
        for t in TOOLS
    ]

    @app.list_tools()
    async def list_tools() -> list[mcp_types.Tool]:
        return descriptors

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[mcp_types.TextContent]:
        if name not in TOOLS_BY_NAME:
            return _to_text_content({"success": False, "error": f"unknown tool: {name}"})

        # First call also covers the "PT not yet running" warmup window.
        if not bridge.is_connected:
            try:
                await bridge.wait_until_connected(timeout=30.0)
            except RuntimeError as exc:
                return _to_text_content({"success": False, "error": str(exc)})

        try:
            args = _normalize_tool_arguments(arguments)
            if name == "addModule":
                result = await call_add_module(bridge, args)
            elif name == "runPcCommand":
                result = await run_pc_command(
                    bridge,
                    device_name=str(args.get("deviceName", "")),
                    command=str(args.get("command", "")),
                    timeout_ms=args.get("timeoutMs") or 15000,
                )
            else:
                result = await bridge.call_tool(name, args)
        except MCPRequestError as exc:
            log.warning("tool %s rejected invalid arguments: %s", name, exc)
            return _to_text_content({"success": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("tool %s failed", name)
            return _to_text_content({"success": False, "error": str(exc)})

        return _to_text_content(result)

    return app


async def run() -> None:
    """Run bridge + MCP stdio server until cancelled."""
    bridge = PTBridge()
    await bridge.start()
    # uv-spawned instances can be orphaned when the wrapper is killed but
    # our stdin pipe stays open — exit with our ancestors then.
    watchdog = asyncio.create_task(orphan_watchdog())
    try:
        app = build_server(bridge)
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options(),
            )
    finally:
        watchdog.cancel()
        await bridge.stop()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="cisco-pt-mcp",
        description="cisco-pt-mcp: MCP server that drives Cisco Packet Tracer via the headless bridge plugin.",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("CISCO_PT_MCP_LOG_LEVEL", "INFO"),
    )
    args = parser.parse_args()

    # Logging goes to stderr; stdout is reserved for stdio MCP framing.
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
