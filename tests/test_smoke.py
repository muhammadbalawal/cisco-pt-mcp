"""Offline smoke tests — run without Packet Tracer."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
import mcp.types as mt

from mcp_server import __version__, bridge, server, tools


EXPECTED_TOOLS = {
    "addDevice", "addModule", "addLink",
    "removeDevice", "removeLink",
    "configurePcIp", "configureIosDevice",
    "getNetwork", "getDeviceInfo",
    "setSimulationMode", "getSimulationStatus", "stepSimulation", "sendPdu",
    "renameDevice", "moveDevice", "setPower",
    "getPduResults", "getCommandLog",
}


def test_tool_set_complete():
    names = {t["name"] for t in tools.TOOLS}
    assert names == EXPECTED_TOOLS
    assert tools.TOOLS_BY_NAME["addDevice"]["inputSchema"]["required"] == [
        "deviceName", "deviceModel", "x", "y",
    ]


def test_schemas_serializable():
    for t in tools.TOOLS:
        json.dumps(t["inputSchema"])
        props = t["inputSchema"].get("properties", {})
        for name, p in props.items():
            assert "type" in p, f"{t['name']}.{name} missing type"


def test_schemas_accepted_by_mcp_types():
    descriptors = [
        mt.Tool(name=t["name"], description=t["description"], inputSchema=t["inputSchema"])
        for t in tools.TOOLS
    ]
    assert len(descriptors) == len(EXPECTED_TOOLS)


@pytest.mark.asyncio
async def test_bridge_lifecycle():
    b = bridge.PTBridge(host="127.0.0.1", port=17531)
    await b.start()
    assert not b.is_connected
    await b.stop()


@pytest.mark.asyncio
async def test_call_tool_without_plugin():
    b = bridge.PTBridge(host="127.0.0.1", port=17532)
    await b.start()
    try:
        with pytest.raises(RuntimeError, match=r"plugin"):
            await b.call_tool("addDevice", {"deviceName": "R1"})
    finally:
        await b.stop()


def test_server_constructs():
    b = bridge.PTBridge(host="127.0.0.1", port=17533)
    s = server.build_server(b)
    assert s.name == "cisco-pt-mcp"


def test_normalize_tool_arguments_accepts_none_and_dict():
    assert server._normalize_tool_arguments(None) == {}
    args = {"deviceName": "R1"}
    assert server._normalize_tool_arguments(args) is args


def test_normalize_tool_arguments_rejects_non_object():
    with pytest.raises(server.MCPRequestError, match="JSON object"):
        server._normalize_tool_arguments(["not", "a", "dict"])


@pytest.mark.asyncio
async def test_bridge_rejects_malformed_tool_result_payload():
    b = bridge.PTBridge(host="127.0.0.1", port=17534)
    future = asyncio.get_running_loop().create_future()
    b._pending["tc-1"] = future

    await b._sio.handlers["/"]["tool_result"]("sid", {"tool_call_id": "tc-1", "result": "bad"})

    with pytest.raises(bridge.PTBridgeProtocolError, match="'result' must be an object"):
        await future


@pytest.mark.asyncio
async def test_bridge_rejects_missing_result_field():
    b = bridge.PTBridge(host="127.0.0.1", port=17535)
    future = asyncio.get_running_loop().create_future()
    b._pending["tc-2"] = future

    await b._sio.handlers["/"]["tool_result"]("sid", {"tool_call_id": "tc-2"})

    with pytest.raises(bridge.PTBridgeProtocolError, match="missing required field 'result'"):
        await future


def test_package_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'^version = "([^"]+)"$', pyproject.read_text(), re.MULTILINE)
    assert match is not None, "version not found in pyproject.toml"
    assert __version__ == match.group(1)
