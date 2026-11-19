# cisco-pt-mcp — agent install instructions

You are setting up the **cisco-pt-mcp** MCP server
(<https://github.com/reisen7/cisco-pt-mcp>, a personal fork of
muhammadbalawal/cisco-pt-mcp) for the user. Work through these steps:

1. **Check `uv` is installed** — run `uv --version`. If missing, install it:
   <https://docs.astral.sh/uv/getting-started/installation/>

2. **Clone the repo** into a permanent directory and remember the path:

   ```sh
   git clone https://github.com/reisen7/cisco-pt-mcp
   ```

   The clone is required — the Packet Tracer extension file
   (`extension/cisco-pt-mcp.pts`) lives in the repo, not in the Python package.

3. **Register a stdio MCP server** named `cisco-pt-mcp` in the MCP config of
   the client you are running in:

   - command: `uvx`
   - args: `["--from", "git+https://github.com/reisen7/cisco-pt-mcp", "cisco-pt-mcp"]`

   Use the client's native mechanism, e.g.:

   - **Claude Code**: `claude mcp add cisco-pt-mcp --scope user -- uvx --from git+https://github.com/reisen7/cisco-pt-mcp cisco-pt-mcp`
   - **Cursor**: `~/.cursor/mcp.json` (or `.cursor/mcp.json` per project)
   - **VS Code**: `.vscode/mcp.json`
   - **Codex CLI**: `~/.codex/config.toml` under `[mcp_servers.cisco-pt-mcp]`
   - **Other clients**: use the client's documented MCP config format.

4. **Print the absolute path** to `extension/cisco-pt-mcp.pts` inside the
   clone, and tell the user to finish the Packet Tracer side by hand:

   - Open Packet Tracer
   - **Extensions → Scripting → Configure PT Script Modules… → Add** → select
     the `.pts` file
   - Restart Packet Tracer

5. **Wrap up** by reminding the user to restart their MCP client, then click
   **Extensions → Packet Tracer MCP** in Packet Tracer — the status pill
   should flip to `connected`.
