# Third-party notices

The project is distributed under AGPL-3.0-or-later. Copied author-owned rendering and question-package code is documented in [provenance](docs/provenance.md). No DeepTutor implementation is included.

| Direct dependency | License | Use |
|---|---|---|
| MCP Python SDK, mcp-types | MIT | MCP protocol and types |
| PyMuPDF (<2) | AGPL-3.0-or-later / commercial dual licensing | PDF QA and image decoding |
| jsonschema | MIT | Answer-package schema |
| numpy | BSD-3-Clause | Optional embedding interface |
| httpx | BSD-3-Clause | Download client |
| Pillow | MIT-CMU | Model-facing image resizing |
| uvicorn, Starlette | BSD-3-Clause | ASGI server |
| pytest, pytest-asyncio | MIT / Apache-2.0 | Development tests |

Exact resolved versions and transitive dependencies are in `uv.lock`; each distribution retains its own license. TeX Live and fonts are installed separately, not redistributed. LICENSE text is the SPDX license-list-data rendering of the GNU AGPL version 3.
