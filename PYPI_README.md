# cyan-eclipse-apply

mcp-name: io.github.CYAN-ECLIPSE-TECHNOLOGIES-LLC/apply

**What this is:** the MCP server for **Eclipse Apply** — an MCP
server that applies to jobs inside a human-granted written policy:
target roles, salary floor, locations, company allowlist +
blocklist, daily cap, approval threshold, expiry and a kill switch.
Discovery is live against the public Greenhouse/Lever/Ashby/Workable
JSON feeds. Review-first by design: matches land as prepared
applications with a tailored resume that re-orders verbatim real
experience only — every tailoring ships with a machine-verifiable
diff proving zero invented facts — and every grant, preparation,
approval, refusal and submission emits a receipt into an
append-only, hash-chained ledger.

## Honest scope

- **v0.1 submission is DRY-RUN ONLY:** the Greenhouse payload is
  built, validated and recorded, and nothing is transmitted to any
  employer.
- State file: `$ECLIPSE_APPLY_STATE` or `./state.json` next to the
  server.
- Certified (sandbox) by the Eclipse Test Bench 2026-10-25, 17/18
  checks pass, 1 na.

## Run it

```console
$ pip install cyan-eclipse-apply
$ eclipse-apply           # MCP over stdio, Python stdlib only
```

MCP client config:

```json
{ "mcpServers": { "cyan-eclipse-apply": { "command": "uvx", "args": ["--from", "cyan-eclipse-apply", "eclipse-apply"] } } }
```

## Tools (13)

`ingest_resume` · `set_policy` · `get_policy` · `search_jobs` · `match_jobs` · `prepare_application` · `approve_application` · `reject_application` · `list_receipts` · `get_tracker` · `pause` · `resume` · `delete_my_data`

Powered by Cyan Eclipse Technologies LLC (cetech.ai) — a Veteran-Owned
Small Business. MIT licensed.
