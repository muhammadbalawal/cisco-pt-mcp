"""cisco-pt-mcp — Packet Tracer MCP server.

Exposes the Packet Tracer toolset (addDevice, addLink, configureIosDevice, ...)
over the Model Context Protocol so any MCP client (Claude Desktop, Cursor,
Backboard, ...) can drive a running Packet Tracer through the bridge
extension shipped under ``extension/source/``.
"""

__version__ = "0.1.5"
