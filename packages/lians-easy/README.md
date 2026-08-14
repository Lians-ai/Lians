# Lians Easy

Lians Easy is the dependency-free desktop runtime behind the guided Lians
installer. It stores memory in local SQLite and exposes it to supported AI
clients through MCP.

Normal users should download the standalone app from GitHub Releases and
double-click it. Developers and IT teams can run the same installer from source:

```bash
python -m lians_easy install --clients claude,cursor,cline --yes
python -m lians_easy doctor --json
```

Supported targets are Claude Desktop, Cursor, Windsurf, Gemini CLI, Codex, and
Cline CLI. Cline uses its documented CLI settings file at
`~/.cline/data/settings/cline_mcp_settings.json`.

No Lians account, API key, database server, model download, or manual JSON
editing is required. The full Lians engine remains available when a team needs
semantic retrieval, governance, or a shared server deployment.
