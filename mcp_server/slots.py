"""addModule slot-path handling.

PT's ``Device.addModule`` takes a slot *path* string (e.g. ``"0/0"``),
not a bare index. Verified live on a 2811: ``slot=0`` fails while
``"0/0"`` succeeds and produces Serial0/0/0 + Serial0/0/1. To keep the
integer form usable, expand it to a short candidate list — each failed
attempt power-cycles the device, so the list stays at three entries.
"""

from __future__ import annotations

from typing import Any


def slot_candidates(slot: Any) -> list[str]:
    """Ordered slot-path candidates to try for a user-supplied slot."""
    if slot is None:
        return []
    if isinstance(slot, bool):
        return [str(slot)]
    if isinstance(slot, int):
        raw = str(slot)
        return _dedupe([raw, f"0/{slot}", f"{slot}/0"])
    if isinstance(slot, str):
        s = slot.strip()
        if not s:
            return []
        if "/" in s:
            return [s]
        if s.isdigit():
            return _dedupe([s, f"0/{s}", f"{s}/0"])
        return [s]
    return [str(slot)]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def call_add_module(bridge, args: dict) -> dict:
    """Try each slot-path candidate until one succeeds."""
    candidates = slot_candidates(args.get("slot")) or [args.get("slot")]
    tried: list[str] = []
    last_error: str | None = None
    for cand in candidates:
        call_args = dict(args)
        call_args["slot"] = cand
        result = await bridge.call_tool("addModule", call_args)
        tried.append(str(cand))
        if isinstance(result, dict) and result.get("success"):
            result["slotUsed"] = cand
            return result
        if isinstance(result, dict) and result.get("error"):
            last_error = str(result["error"])

    detail = f" Last PT error: {last_error}" if last_error else ""
    return {
        "success": False,
        "error": (
            f"Failed to add module {args.get('model')} to "
            f"{args.get('deviceName')}: every slot path failed. "
            f"'slot' is a path string, not a bare index — e.g. a WIC/HWIC "
            f"bay on a 2811 is \"0/0\" (verified live), sub-slots look like "
            f"\"0/1\". Tried: {tried}. If none of these work, install the "
            f"module by hand (device -> Physical tab)." + detail
        ),
        "tried": tried,
    }
