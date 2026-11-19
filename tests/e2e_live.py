"""Live end-to-end check against a running Packet Tracer instance.

Not part of the pytest suite (filename intentionally does not match
``test_*.py``). Drives the real path — PTBridge socket.io -> PT extension ->
Packet Tracer API — without needing an MCP client.

Usage:
    1. Close MCP clients using cisco-pt-mcp (the bridge port 7531 must be free).
    2. Open Packet Tracer with an empty workspace.
    3. In PT: Extensions -> Packet Tracer MCP (opens the bridge window).
    4. Run:  python tests/e2e_live.py [--cleanup]
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp_server.bridge import PTBridge

DEVICE_NAMES = ["SW1", "PC1", "PC2", "R1", "R2"]
failed = 0


def show(title: str, result: dict) -> None:
    global failed
    ok = isinstance(result, dict) and result.get("success", True) is not False
    if not ok:
        failed += 1
    print(f"[{'ok ' if ok else 'ERR'}] {title}: {json.dumps(result, default=str)[:600]}")


async def call(bridge: PTBridge, tool: str, args: dict) -> dict:
    result = await bridge.call_tool(tool, args)
    show(tool, result)
    return result


def port_names(result: dict, prefix: str) -> list[str]:
    """Interface names starting with `prefix`, free (not in_use) first."""
    dev = (result or {}).get("result", {}).get("device", {})
    ifaces = dev.get("interfaces", [])
    names = [i["name"] for i in ifaces if i["name"].startswith(prefix)]
    free = [i["name"] for i in ifaces if i["name"].startswith(prefix) and not i.get("in_use")]
    return free or names


async def main() -> int:
    global failed
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true",
                        help="remove the test devices at the end")
    args = parser.parse_args()

    bridge = PTBridge()
    try:
        await bridge.start()
    except OSError as exc:
        print(f"cannot bind bridge port 7531: {exc}")
        print("close any MCP client already running cisco-pt-mcp and retry.")
        return 1

    print("bridge listening on :7531 — open PT -> Extensions -> Packet Tracer MCP")
    try:
        await bridge.wait_until_connected(timeout=300)
    except RuntimeError as exc:
        print(exc)
        await bridge.stop()
        return 1
    print("PT extension connected\n")

    try:
        await call(bridge, "getNetwork", {})

        # 1) buildTopology: devices + links + PC IP in one call.
        await call(bridge, "buildTopology", {
            "devices": [
                {"deviceName": "SW1", "deviceModel": "2960-24TT", "x": 300, "y": 220},
                {"deviceName": "PC1", "deviceModel": "PC-PT", "x": 120, "y": 340},
                {"deviceName": "PC2", "deviceModel": "PC-PT", "x": 480, "y": 340},
                {"deviceName": "R1", "deviceModel": "2911", "x": 300, "y": 80},
                {"deviceName": "R2", "deviceModel": "2911", "x": 620, "y": 80},
            ],
            "links": [
                {"device1Name": "PC1", "device1Interface": "FastEthernet0",
                 "device2Name": "SW1", "device2Interface": "FastEthernet0/1",
                 "linkType": "auto"},
                {"device1Name": "PC2", "device1Interface": "FastEthernet0",
                 "device2Name": "SW1", "device2Interface": "FastEthernet0/2",
                 "linkType": "straight"},
            ],
            "pcConfigs": [
                {"deviceName": "PC1", "ipaddress": "192.168.1.10",
                 "subnetMask": "255.255.255.0", "defaultGateway": "192.168.1.1"},
            ],
        })

        # 2) console cable PC1 <-> R1. Console/RS232 ports are hidden from
        # getPortCount but createLink resolves them by name ("RS 232" on the
        # PC, "Console" on the router) — addLink also retries misspellings.
        console_link = await bridge.call_tool("addLink", {
            "device1Name": "PC1", "device1Interface": "RS 232",
            "device2Name": "R1", "device2Interface": "Console",
            "linkType": "console"})
        if console_link.get("success"):
            print(f"[ok ] console link: {console_link}")
        else:
            failed += 1
            print(f"[ERR] console link failed: {console_link}")

        # 2b) same console pair via a misspelled name — the hidden-name
        # fallback should still connect it (would be a duplicate cable, so
        # only run if step 2 failed and report instead of counting).
        if not console_link.get("success"):
            retry = await bridge.call_tool("addLink", {
                "device1Name": "PC1", "device1Interface": "RS232",
                "device2Name": "R1", "device2Interface": "Console",
                "linkType": "console"})
            print(f"[..] console retry with 'RS232': {retry}")

        # 3) serial cable on ethernet ports — must FAIL with the new hint.
        print("-- deliberate failure: serial cable on copper ports --")
        bad = await bridge.call_tool("addLink", {
            "device1Name": "R1", "device1Interface": "GigabitEthernet0/0",
            "device2Name": "R2", "device2Interface": "GigabitEthernet0/0",
            "linkType": "serial"})
        if bad.get("success"):
            failed += 1
            print("[ERR] expected this link to fail but it succeeded")
        else:
            print(f"[ok ] addLink correctly rejected: "
                  f"{str(bad.get('error'))[:300]}")

        # 4) install WIC-2T on both routers, then a real serial link.
        for router in ("R1", "R2"):
            for slot in range(4):
                res = await call(bridge, "addModule", {
                    "deviceName": router, "slot": slot, "model": "WIC-2T"})
                if res.get("success"):
                    break
            else:
                failed += 1
                print(f"[ERR] no WIC-2T slot accepted on {router}")

        r1 = await call(bridge, "getDeviceInfo", {"deviceName": "R1"})
        r2 = await call(bridge, "getDeviceInfo", {"deviceName": "R2"})
        r1_se, r2_se = port_names(r1, "Serial"), port_names(r2, "Serial")
        if r1_se and r2_se:
            await call(bridge, "addLink", {
                "device1Name": "R1", "device1Interface": r1_se[0],
                "device2Name": "R2", "device2Interface": r2_se[0],
                "linkType": "serial"})
        else:
            # No serial ports enumerated — either no module was installed or
            # the serial ports are hidden too. addLink retries hidden
            # Serial0/x/y names itself; without a module it must still fail,
            # but with the "install a module" hint.
            se = await bridge.call_tool("addLink", {
                "device1Name": "R1", "device1Interface": "Serial0/0/0",
                "device2Name": "R2", "device2Interface": "Serial0/0/0",
                "linkType": "serial"})
            if se.get("success"):
                print(f"[ok ] serial link via hidden names: {se}")
            else:
                print(f"[ok ] serial correctly needs a module: "
                      f"{str(se.get('error'))[:200]}")

        await call(bridge, "getNetwork", {})

        if args.cleanup:
            await call(bridge, "removeDevice", {"deviceNames": DEVICE_NAMES})

        print(f"\ndone — {failed} failure(s). "
              f"{'Left devices in workspace.' if not args.cleanup else 'Cleaned up.'}")
        return 1 if failed else 0
    finally:
        await bridge.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
