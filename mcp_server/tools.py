"""Tool schemas exposed to MCP clients.

Tool names match the JS function names in ``extension/source/userfunctions.js``
exactly so ``bridge.py`` can forward by name without translation.
"""

from __future__ import annotations

# IOS interface naming pattern reused by addLink / removeLink. The second
# branch covers ports PT never enumerates but still resolves by name in
# createLink ("RS 232", USB, Coaxial...).
INTERFACE_PATTERN = (
    r"^((GigabitEthernet|FastEthernet|Ethernet|Serial|Console|RS232|AUX|Vlan|Port|Gi|Fa|Se)"
    r"( ?\d+)?(/\d+){0,2}|RS ?232|RS-232|Aux|USB ?\d*|Coaxial ?\d*|Cellular ?\d*)$"
)

# Cable types accepted by addLink / buildTopology. Keep aligned with
# allLinkTypes in extension/source/links.js (aliases excluded).
LINK_TYPES = [
    "straight", "cross", "roll", "fiber", "phone", "cable",
    "serial", "auto", "console", "wireless", "coaxial",
    "octal", "cellular", "usb", "custom_io",
]

TOOLS: list[dict] = [
    {
        "name": "addDevice",
        "description": (
            "Add a single network device to the workspace. For multiple devices "
            "plus links/IPs, prefer buildTopology (one round-trip). Call "
            "getNetwork first if unsure whether the chosen deviceName is already "
            "taken; names must be unique."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {
                    "type": "string",
                    "description": "Unique name for the device (must not already exist).",
                },
                "deviceModel": {
                    "type": "string",
                    "enum": [
                        "2911", "1941", "2811", "2901",
                        "2960-24TT", "2950-24", "3560-24PS",
                        "PC-PT", "Server-PT", "Laptop-PT", "Printer-PT",
                        "Hub-PT",
                    ],
                    "description": (
                        "Device model. Router: 2911/1941/2811/2901. "
                        "Switch: 2960-24TT/2950-24/3560-24PS. "
                        "End device: PC-PT/Server-PT/Laptop-PT/Printer-PT."
                    ),
                },
                "x": {"type": "number", "description": "X coordinate on workspace."},
                "y": {"type": "number", "description": "Y coordinate on workspace."},
            },
            "required": ["deviceName", "deviceModel", "x", "y"],
        },
    },
    {
        "name": "addModule",
        "description": (
            "Add an interface module to a device. Powers the device off, "
            "installs the module, powers it back on. NOTE: PT often does "
            "not expose module bays via the API — if this fails, the "
            "module must be installed by hand (device -> Physical tab)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string", "description": "Existing device name."},
                "slot": {
                    "type": ["integer", "string"],
                    "description": "Slot index (e.g. 0-3) or slot path string.",
                },
                "model": {
                    "type": "string",
                    "description": "Module model, e.g. HWIC-2T (serial), NM-4E (ethernet).",
                },
            },
            "required": ["deviceName", "slot", "model"],
        },
    },
    {
        "name": "addLink",
        "description": (
            "Connect two devices with a cable. Both interfaces must be free "
            "(in_use=false in getNetwork output). If unsure, call getNetwork "
            "first."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "device1Name": {"type": "string"},
                "device1Interface": {
                    "type": "string",
                    "pattern": INTERFACE_PATTERN,
                    "description": "Free interface on device 1.",
                },
                "device2Name": {"type": "string"},
                "device2Interface": {
                    "type": "string",
                    "pattern": INTERFACE_PATTERN,
                    "description": "Free interface on device 2.",
                },
                "linkType": {
                    "type": "string",
                    "enum": LINK_TYPES,
                    "description": (
                        "Cable type. 'auto' resolves the type from the two "
                        "port types (recommended default). 'console' uses "
                        "the hidden ports \"RS 232\" (PC) and \"Console\" "
                        "(router) — they are not listed by getDeviceInfo but "
                        "connect fine by name; misspellings are retried "
                        "automatically. 'serial' needs a WIC-2T/HWIC-2T/"
                        "NIM-2T module installed by hand (Physical tab — "
                        "bays are not API-exposed), then Serial0/x/y names "
                        "are tried automatically. usb/custom_io/cellular/"
                        "octal are for IoT and end devices."
                    ),
                },
            },
            "required": ["device1Name", "device1Interface", "device2Name", "device2Interface", "linkType"],
        },
    },
    {
        "name": "removeDevice",
        "description": "Remove one or more devices from the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceNames": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Array of existing device names to remove.",
                },
            },
            "required": ["deviceNames"],
        },
    },
    {
        "name": "removeLink",
        "description": "Remove one or more cables. Each entry identifies one endpoint of the link.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "links": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "device": {"type": "string"},
                            "port": {"type": "string", "pattern": INTERFACE_PATTERN},
                        },
                        "required": ["device", "port"],
                    },
                    "description": "Array of {device, port} endpoints.",
                },
            },
            "required": ["links"],
        },
    },
    {
        "name": "configurePcIp",
        "description": "Configure IP settings on a PC or end device.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string", "description": "PC name (must exist)."},
                "dhcpEnabled": {"type": "boolean", "description": "True for DHCP, false for static IP."},
                "ipaddress": {"type": "string", "description": "IP address (if static)."},
                "subnetMask": {"type": "string", "description": "Subnet mask (if static)."},
                "defaultGateway": {"type": "string", "description": "Default gateway."},
                "dnsServer": {"type": "string", "description": "DNS server."},
            },
            "required": ["deviceName"],
        },
    },
    {
        "name": "configureIosDevice",
        "description": (
            "Execute IOS commands on a router or switch. Commands run from "
            "global config and are saved with 'write memory'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string"},
                "commands": {
                    "type": "string",
                    "description": "Newline-separated IOS commands.",
                },
            },
            "required": ["deviceName", "commands"],
        },
    },
    {
        "name": "getNetwork",
        "description": (
            "Snapshot of the entire workspace: every device, its interfaces "
            "(with in_use flags), and every cable. Hidden ports (Console, "
            "\"RS 232\", WIC serials) appear in interfaces once a cable "
            "uses them. Call this before addLink or removeDevice when "
            "unsure of current state."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "getDeviceInfo",
        "description": "Detailed info about one device, including interfaces and incident links.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string"},
            },
            "required": ["deviceName"],
        },
    },
    {
        "name": "setSimulationMode",
        "description": (
            "Switch Packet Tracer between simulation mode and realtime mode. "
            "Simulation mode is required before sending PDUs or stepping through traffic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "toSimMode": {
                    "type": "boolean",
                    "description": "true = simulation mode, false = realtime mode.",
                },
            },
            "required": ["toSimMode"],
        },
    },
    {
        "name": "getSimulationStatus",
        "description": (
            "Query the current simulation state: mode (realtime or simulation), "
            "elapsed simulation time, PDU frame count, and current frame index."
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "stepSimulation",
        "description": (
            "Step the simulation forward or backward, or reset it entirely. "
            "PT must already be in simulation mode (use setSimulationMode first)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["forward", "backward", "reset"],
                    "description": (
                        "'forward' advances one step, 'backward' goes back one step, "
                        "'reset' clears all PDUs and returns to time 0."
                    ),
                },
                "steps": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Number of steps to take (ignored for 'reset'). Defaults to 1.",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "sendPdu",
        "description": (
            "Add an ICMP ping PDU between two devices using PT's native Simple PDU mechanism. "
            "Automatically enables simulation mode if not already active. "
            "Use stepSimulation to advance and getPduResults to check if the ping succeeded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sourceDevice": {
                    "type": "string",
                    "description": "Name of the source device.",
                },
                "destinationDevice": {
                    "type": "string",
                    "description": "Name of the destination device.",
                },
            },
            "required": ["sourceDevice", "destinationDevice"],
        },
    },
    {
        "name": "renameDevice",
        "description": "Rename an existing device. The new name must be unique in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string", "description": "Current device name."},
                "newName": {"type": "string", "description": "New unique name for the device."},
            },
            "required": ["deviceName", "newName"],
        },
    },
    {
        "name": "moveDevice",
        "description": "Move a device to new coordinates on the logical workspace canvas.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string", "description": "Device to reposition."},
                "x": {"type": "number", "description": "New X coordinate."},
                "y": {"type": "number", "description": "New Y coordinate."},
            },
            "required": ["deviceName", "x", "y"],
        },
    },
    {
        "name": "setPower",
        "description": "Power a device on or off.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {"type": "string", "description": "Device name."},
                "power": {
                    "type": "boolean",
                    "description": "true = power on, false = power off.",
                },
            },
            "required": ["deviceName", "power"],
        },
    },
    {
        "name": "getPduResults",
        "description": (
            "Read the outcome of PDUs in the current simulation — "
            "source, destination, traffic type, and status (accepted/dropped/in_transit/etc). "
            "Call after stepSimulation to verify connectivity. "
            "Use the types filter to show only ICMP, TCP, etc. and hide STP/DTP background noise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Only return frames matching these traffic type names "
                        "(e.g. [\"ICMP\"], [\"ICMP\",\"ARP\"]). Omit to return all frames."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "getCommandLog",
        "description": (
            "Read the IOS command history logged by Packet Tracer. "
            "Optionally filter by device name and cap the number of returned entries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "deviceName": {
                    "type": "string",
                    "description": "Return only entries from this device. Omit to return all devices.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "Maximum entries to return, newest first. Defaults to 50.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "buildTopology",
        "description": (
            "Build a topology in ONE call: add devices, create links, and optionally "
            "configure PC IPs. Prefer this over many sequential addDevice/addLink/"
            "configurePcIp calls — it is much faster because it avoids repeated MCP "
            "round-trips. Order: devices first, then links, then pcConfigs."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "devices": {
                    "type": "array",
                    "description": "Devices to add, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "deviceName": {"type": "string"},
                            "deviceModel": {
                                "type": "string",
                                "enum": [
                                    "2911", "1941", "2811", "2901",
                                    "2960-24TT", "2950-24", "3560-24PS",
                                    "PC-PT", "Server-PT", "Laptop-PT", "Printer-PT",
                                    "Hub-PT",
                                ],
                            },
                            "x": {"type": "number"},
                            "y": {"type": "number"},
                        },
                        "required": ["deviceName", "deviceModel", "x", "y"],
                    },
                },
                "links": {
                    "type": "array",
                    "description": (
                        "Cables to create after devices exist. Same rules "
                        "as addLink: prefer 'auto'; console uses "
                        "\"RS 232\"<->\"Console\"; serial needs a hand-"
                        "installed WIC/HWIC module first."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "device1Name": {"type": "string"},
                            "device1Interface": {
                                "type": "string",
                                "pattern": INTERFACE_PATTERN,
                            },
                            "device2Name": {"type": "string"},
                            "device2Interface": {
                                "type": "string",
                                "pattern": INTERFACE_PATTERN,
                            },
                            "linkType": {
                                "type": "string",
                                "enum": LINK_TYPES,
                            },
                        },
                        "required": [
                            "device1Name",
                            "device1Interface",
                            "device2Name",
                            "device2Interface",
                            "linkType",
                        ],
                    },
                },
                "pcConfigs": {
                    "type": "array",
                    "description": "Optional PC/end-device IP settings applied after links.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "deviceName": {"type": "string"},
                            "dhcpEnabled": {"type": "boolean"},
                            "ipaddress": {"type": "string"},
                            "subnetMask": {"type": "string"},
                            "defaultGateway": {"type": "string"},
                            "dnsServer": {"type": "string"},
                        },
                        "required": ["deviceName"],
                    },
                },
                "stopOnError": {
                    "type": "boolean",
                    "description": (
                        "If true (default), stop at the first failed step. "
                        "If false, continue and report all failures."
                    ),
                },
            },
            "required": [],
        },
    },
]

TOOLS_BY_NAME: dict[str, dict] = {t["name"]: t for t in TOOLS}
