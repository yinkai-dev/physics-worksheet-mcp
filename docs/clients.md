# Clients

The official MCP Python SDK supports both modern (`auto`) and handshake-era (`legacy`) connections to the same endpoint. Integration tests exercise both over real HTTP. Run the repository smoke client with credentials in an ignored file:

```sh
python scripts/sdk_client.py --url https://worksheet.example.org/mcp --token-file private/tokens/demo.txt --out private/downloads --mode auto
```

Codex configuration (restart or reconnect the client after adding):

```toml
[mcp_servers.physics_worksheet]
url = "https://worksheet.example.org/mcp"
bearer_token_env_var = "PWMCP_TOKEN"
startup_timeout_sec = 20
tool_timeout_sec = 120
```

Set PWMCP_TOKEN in the user environment without copying it into a tracked file or conversation. List available banks, request a worksheet, poll its job, download the URL and compare SHA256. Only actually opening the local PDF completes client acceptance.

Qoder CLI example (private settings file):

```json
{"mcpServers":{"physics-worksheet":{"type":"http","url":"https://worksheet.example.org/mcp","headers":{"Authorization":"Bearer <private-token>"},"timeout":120000}}}
```

Qoder IDE header support needs direct validation in the installed version. SDK success is not evidence of Codex or Qoder UI compatibility. Browser download URLs contain short-lived single-file bearer capabilities; do not share them publicly.

Documentation sources: [official Python SDK legacy support](https://py.sdk.modelcontextprotocol.io/run/legacy-clients/), [Codex configuration](https://developers.openai.com/codex/config-reference), [Qoder MCP](https://docs.qoder.com/cli/mcp-reference).
