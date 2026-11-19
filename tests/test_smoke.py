"""Offline smoke tests — run without Packet Tracer."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
import mcp.types as mt

from mcp_server import __version__, bridge, pc, server, slots, tools


EXPECTED_TOOLS = {
    "addDevice", "addModule", "addLink",
    "removeDevice", "removeLink",
    "configurePcIp", "configureIosDevice",
    "getNetwork", "getDeviceInfo",
    "setSimulationMode", "getSimulationStatus", "stepSimulation", "sendPdu",
    "renameDevice", "moveDevice", "setPower",
    "getPduResults", "getCommandLog",
    "buildTopology", "runPcCommand",
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


@pytest.mark.asyncio
async def test_bridge_decodes_string_result_payload():
    b = bridge.PTBridge(host="127.0.0.1", port=17536)
    future = asyncio.get_running_loop().create_future()
    b._pending["tc-3"] = future

    await b._sio.handlers["/"]["tool_result"]("sid", {
        "tool_call_id": "tc-3",
        "result": json.dumps({"success": True, "message": "ok"}),
    })

    assert await future == {"success": True, "message": "ok"}


@pytest.mark.asyncio
async def test_bridge_decodes_double_encoded_result():
    b = bridge.PTBridge(host="127.0.0.1", port=17537)
    future = asyncio.get_running_loop().create_future()
    b._pending["tc-4"] = future

    await b._sio.handlers["/"]["tool_result"]("sid", {
        "tool_call_id": "tc-4",
        "result": json.dumps(json.dumps({"success": True})),
    })

    assert await future == {"success": True}


@pytest.mark.asyncio
async def test_bridge_collapses_runcode_envelope():
    b = bridge.PTBridge(host="127.0.0.1", port=17538)
    future = asyncio.get_running_loop().create_future()
    b._pending["tc-5"] = future

    await b._sio.handlers["/"]["tool_result"]("sid", {
        "tool_call_id": "tc-5",
        "result": {"success": True, "result": {"deviceCount": 0}, "code": "return getNetwork();"},
    })

    assert await future == {"deviceCount": 0}


def test_slot_candidates():
    assert slots.slot_candidates(0) == ["0", "0/0"]
    assert slots.slot_candidates(1) == ["1", "0/1", "1/0"]
    assert slots.slot_candidates("2") == ["2", "0/2", "2/0"]
    assert slots.slot_candidates("0/0") == ["0/0"]
    assert slots.slot_candidates("0/1") == ["0/1"]
    assert slots.slot_candidates("weird-slot") == ["weird-slot"]


class _FakeBridge:
    """Records calls; handler returns the next result per tool name."""

    def __init__(self, handlers):
        self.calls = []
        self._handlers = handlers

    async def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        return self._handlers[name](args)


@pytest.mark.asyncio
async def test_add_module_retries_slot_paths():
    def handler(args):
        if args["slot"] == "0/1":
            return {"success": True, "message": "ok"}
        return {"success": False, "error": "nope"}

    b = _FakeBridge({"addModule": handler})
    res = await slots.call_add_module(b, {"deviceName": "R1", "slot": 1, "model": "WIC-2T"})
    assert res["success"] is True
    assert res["slotUsed"] == "0/1"
    assert [a["slot"] for _, a in b.calls] == ["1", "0/1"]


@pytest.mark.asyncio
async def test_add_module_all_candidates_fail():
    b = _FakeBridge({"addModule": lambda args: {"success": False, "error": "nope"}})
    res = await slots.call_add_module(b, {"deviceName": "R1", "slot": 1, "model": "WIC-2T"})
    assert res["success"] is False
    assert res["tried"] == ["1", "0/1", "1/0"]
    assert "0/1" in res["error"] and '"0/0"' in res["error"]
    assert "bays" not in res["error"]


@pytest.mark.asyncio
async def test_add_module_explicit_path_single_attempt():
    b = _FakeBridge({"addModule": lambda args: {"success": False, "error": "nope"}})
    res = await slots.call_add_module(b, {"deviceName": "R1", "slot": "0/2", "model": "WIC-2T"})
    assert res["tried"] == ["0/2"]
    assert len(b.calls) == 1


@pytest.mark.asyncio
async def test_run_pc_command_polls_until_stable():
    outputs = iter(["Pinging", "Pinging\r\nReply"])

    def read(_args):
        return {"success": True, "output": next(outputs, "Pinging\r\nReply"), "prompt": "C:\\>"}

    b = _FakeBridge({
        "startPcCommand": lambda _a: {"success": True, "baseline": 0},
        "readPcOutput": read,
    })
    res = await pc.run_pc_command(
        b, "PC1", "ping 192.168.1.2", timeout_ms=5000,
        poll_interval=0.01, stable_window=0.03,
    )
    assert res == {"success": True, "output": "Pinging\r\nReply", "prompt": "C:\\>"}
    assert b.calls[0][0] == "startPcCommand"


@pytest.mark.asyncio
async def test_run_pc_command_times_out_on_growing_output():
    n = 0

    def read(_args):
        nonlocal n
        n += 1
        return {"success": True, "output": "x" * n, "prompt": "C:\\>"}

    b = _FakeBridge({
        "startPcCommand": lambda _a: {"success": True, "baseline": 0},
        "readPcOutput": read,
    })
    res = await pc.run_pc_command(
        b, "PC1", "ping -t 1.1.1.1", timeout_ms=150,
        poll_interval=0.01, stable_window=5.0,
    )
    assert res["success"] is True
    assert res["timedOut"] is True


@pytest.mark.asyncio
async def test_run_pc_command_start_failure():
    b = _FakeBridge({
        "startPcCommand": lambda _a: {"success": False, "error": "no command line"},
    })
    res = await pc.run_pc_command(b, "R1", "ping 1.1.1.1")
    assert res == {"success": False, "error": "no command line"}


@pytest.mark.asyncio
async def test_bridge_bind_conflict_raises_runtime_error(monkeypatch):
    monkeypatch.setattr(bridge, "BIND_WAIT_S", 1.0)
    b1 = bridge.PTBridge(host="127.0.0.1", port=17532)
    await b1.start()
    try:
        b2 = bridge.PTBridge(host="127.0.0.1", port=17532)
        with pytest.raises(RuntimeError, match=r"17532"):
            await b2.start()
    finally:
        await b1.stop()


@pytest.mark.asyncio
async def test_bridge_bind_waits_for_release(monkeypatch):
    monkeypatch.setattr(bridge, "BIND_WAIT_S", 5.0)
    b1 = bridge.PTBridge(host="127.0.0.1", port=17533)
    await b1.start()

    async def release_soon():
        await asyncio.sleep(0.5)
        await b1.stop()

    task = asyncio.create_task(release_soon())
    try:
        b2 = bridge.PTBridge(host="127.0.0.1", port=17533)
        await b2.start()  # must succeed inside the wait window
        await b2.stop()
    finally:
        await task


def test_package_version_matches_pyproject():
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    match = re.search(r'^version = "([^"]+)"$', pyproject.read_text(), re.MULTILINE)
    assert match is not None, "version not found in pyproject.toml"
    assert __version__ == match.group(1)
