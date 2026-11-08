# Physics Worksheet MCP

An authenticated MCP service that selects questions from administrator-installed immutable bank releases and renders source-bound PDFs using XeLaTeX. Question banks are private, separately supplied data; this repository includes only original synthetic engineering fixtures.

Python 3.12 (64-bit), SQLite FTS5 and XeLaTeX with `ctexart` are required. Windows can run non-TeX tests; use Linux/WSL for rendering.

```sh
uv sync --locked
uv run pytest
uv run python scripts/make_demo.py
uv run pwmcp serve --config deploy/config.example.toml
# A second terminal, from the same working directory:
uv run pwmcp worker --config deploy/config.example.toml
```

Use the administrator CLI to create users, grant banks and issue token files. Run `pwmcp admin --help`. No administrative tools are exposed through MCP. The endpoint is `/mcp`; health endpoints are `/healthz` and `/readyz`.

Only deterministic filters and completed scope reviews establish coverage within their scope. Search always reports `coverage: partial`. Zero matches produce no job or PDF. Shortfalls are explicit. Content and answers always come from validated server-side packages.

See [tools](docs/tools.md), [deployment](docs/deploy.md), [clients](docs/clients.md) and [provenance](docs/provenance.md). Source is available under AGPL-3.0-or-later; data rights and teaching quality remain separate from engineering validation.
