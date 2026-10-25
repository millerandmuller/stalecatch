# Hermes on NVIDIA NemoClaw, with Token Factory and the memory MCP server

Verified on macOS (Apple Silicon, Docker Desktop), NemoHermes v0.0.137, Hermes Agent v0.21.3. In five scripted runs the agent called
`remember_from_url` and `derive` through the managed MCP bridge and produced one donor plus three products with correct parents and one
sent message each time (19 to 27 s per run).

## 1. Serve the memory server over HTTPS with a bearer token

NemoClaw's managed MCP accepts only an authenticated HTTPS Streamable HTTP endpoint and rejects loopback. Run the memory server on a private
LAN address with a certificate whose SAN is that IP, signed by a CA that has `basicConstraints=CA:TRUE`:

```bash
export MCP_BEARER_TOKEN="$(openssl rand -hex 24)"
python -m donor_lookback.mcp_server --host <LAN-IP> --port 8770 --tls-cert srv.pem --tls-key srv.key
```

The server refuses to listen beyond loopback without `MCP_BEARER_TOKEN`. Set `MCP_ALLOWED_HOSTS=<LAN-IP>:8770` for the host-header check.

## 2. Onboard Hermes with Token Factory as the custom endpoint

```bash
export NEMOCLAW_AGENT=hermes NEMOCLAW_NON_INTERACTIVE=1 NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1
export NEMOCLAW_SANDBOX_NAME=donor-hermes NEMOCLAW_PROVIDER=custom
export NEMOCLAW_ENDPOINT_URL=https://api.tokenfactory.nebius.com/v1   # no trailing slash
export NEMOCLAW_MODEL=nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B
export COMPATIBLE_API_KEY=<Token Factory key>
export NEMOCLAW_CORPORATE_CA_BUNDLE=/path/to/ca.pem                      # the CA from step 1
export NEMOCLAW_TOOL_DISCLOSURE=direct                                   # see "Gotchas"
curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
```

## 3. Register the memory server and run a scripted turn

```bash
export DONOR_LOOKBACK_MCP_TOKEN=<the bearer token>
nemohermes donor-hermes mcp add donor-lookback --url https://<LAN-IP>:8770/mcp \
  --env DONOR_LOOKBACK_MCP_TOKEN --trusted-private-host <LAN-IP>
nemohermes donor-hermes mcp status donor-lookback          # expect: credential resolution: verified (HTTP 200)
nemohermes donor-hermes exec -- hermes -z "Use the donor-lookback memory tools. ..."
```

## Gotchas

- Set `NEMOCLAW_TOOL_DISCLOSURE=direct` at onboarding. With the default progressive disclosure the agent wrapped the memory tools as deferred
  tools and, in one of three runs, ended after two of three saves. With direct disclosure 5 of 5 runs were complete.
- Do not use `rebuild` on this version to change settings: after a rebuild the in-sandbox model key was stripped and every inference call
  returned 401, with Hermes caching the failure in its credential pool (`hermes auth reset custom:compatible-endpoint` clears the cache but not
  the cause). Destroy the sandbox and onboard fresh instead. `nemohermes credentials reset donor-hermes-mcp-donor-lookback --yes` removes the
  detached MCP provider a destroyed sandbox leaves behind.
- Nano answers in about 1.7 s per call with reasoning off (`NANO_THINKING=0`, sent as `chat_template_kwargs.enable_thinking=false`) versus about 16 s
  with reasoning on.
