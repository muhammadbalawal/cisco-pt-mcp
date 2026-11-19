"""runPcCommand — run a command in a PC/Laptop Command Prompt.

The PT side exposes two bridge functions: ``startPcCommand`` (records the
current output length as a baseline and issues ``enterCommand`` without
blocking) and ``readPcOutput`` (returns output since the baseline plus the
current prompt). PC commands like ``ping`` run asynchronously for several
seconds, so the Python side polls until the output stops growing and the
prompt returns to a ``C:\\>``-style form (or the timeout hits).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

PC_PROMPT_RE = re.compile(r"^[A-Za-z]:\\.*>\s*$")
PC_TAIL_RE = re.compile(r"(?:^|\n)[A-Za-z]:\\[^\r\n]*>\s*$")

DEFAULT_TIMEOUT_MS = 15000
MAX_TIMEOUT_MS = 60000
POLL_INTERVAL_S = 0.5
STABLE_WINDOW_S = 1.5


async def run_pc_command(
    bridge,
    device_name: str,
    command: str,
    timeout_ms: int | float = DEFAULT_TIMEOUT_MS,
    *,
    poll_interval: float = POLL_INTERVAL_S,
    stable_window: float = STABLE_WINDOW_S,
) -> dict[str, Any]:
    timeout_ms = max(0, min(int(timeout_ms or DEFAULT_TIMEOUT_MS), MAX_TIMEOUT_MS))
    started = await bridge.call_tool(
        "startPcCommand", {"deviceName": device_name, "command": command}
    )
    if not isinstance(started, dict) or not started.get("success"):
        return started if isinstance(started, dict) else {
            "success": False,
            "error": f"startPcCommand failed: {started!r}",
        }

    deadline = time.monotonic() + timeout_ms / 1000.0
    last_output = ""
    last_prompt = ""
    last_change = time.monotonic()

    while True:
        await asyncio.sleep(poll_interval)
        read = await bridge.call_tool("readPcOutput", {"deviceName": device_name})
        if not isinstance(read, dict) or not read.get("success"):
            return read if isinstance(read, dict) else {
                "success": False,
                "error": f"readPcOutput failed: {read!r}",
            }
        output = str(read.get("output", ""))
        prompt = str(read.get("prompt", ""))
        now = time.monotonic()
        if output != last_output:
            last_output = output
            last_change = now
        last_prompt = prompt

        stable = now - last_change >= stable_window
        prompt_done = bool(PC_TAIL_RE.search(output))
        if stable and prompt_done:
            return {"success": True, "output": output, "prompt": prompt}
        if now >= deadline:
            return {
                "success": True,
                "output": output,
                "prompt": prompt,
                "timedOut": True,
            }
