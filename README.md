# dns-mcp

`dns-mcp` is a small, **provider-agnostic** [Model Context
Protocol](https://modelcontextprotocol.io) server that exposes DNS record CRUD as
typed tools: *list a zone's records, get one, and create / update / delete
records*. It turns ad-hoc "go poke the DNS API" scripts into constrained,
structured tools an agent can call.

The server is **provider-agnostic by design**: it hard-codes no DNS provider. A
zone's records are served entirely by the injected backend, so the same server
works against whatever provider infra wires up (e.g. Porkbun or Google Cloud
DNS).

> **Repo structure:** this ships as a per-server repo, following the shipped
> convention (e.g. `telemetry-mcp`, `reddit-mcp`, `dispatch-mcp`). Whether the
> fleet's MCP servers consolidate into a single `agent-mcp` repo is pending a
> consolidation decision; until that lands, this stays per-server.

## Tools

| Tool | Purpose |
| --- | --- |
| `dns_list_records(zone)` | List every DNS record in the zone (sorted by name then type). |
| `dns_get_record(zone, name, type)` | Get the record identified by `name` + `type`. |
| `dns_create_record(zone, name, type, value, ttl?, priority?)` | Create a record; the backend rejects a conflict. |
| `dns_update_record(zone, name, type, value, ttl?, priority?)` | Update the record identified by `name` + `type`. |
| `dns_delete_record(zone, name, type)` | Delete the record identified by `name` + `type`. |

`zone` is the apex domain (e.g. `example.com`). `name` is the record name — `@`
for the apex, `*` for a wildcard, or a host label / FQDN. `type` is a supported RR
type (`A`/`AAAA`/`CNAME`/`TXT`/`MX`/`NS`/`SRV`/`CAA`/`ALIAS`/`PTR`). `ttl`
(seconds) is optional — `None` lets the provider default it. `priority` is used by
`MX` / `SRV`.

## Security model

This server is built so that exposing it does **not** expose arbitrary provider
access or command execution. The properties below are enforced in code and
covered by tests.

- **Bounded surface.** The tools are list/get/create/update/delete over a
  validated record model. There is no raw provider-call escape hatch — a caller
  cannot supply arbitrary API text.
- **No embedded credentials.** Nothing in this package stores a token or key.
  Credentials are resolved **at call time** by an injected `CredentialProvider`
  (backed by WIF/GSM/env in production) and handed to the backend per request;
  they never live in source, in the service, or in a returned payload (tests
  assert the sentinel credential never appears in output).
- **Validated inputs.** Zone, record name, type, and TTL are restricted to a
  conservative shape (RR types are an allowlist), so a rejected request cannot
  smuggle injection into the backend (defence in depth; the provider's own
  validation is the real gate).
- **Provider-agnostic.** No provider is hard-coded; the backend defines how
  records are stored and served, so the server cannot leak provider-specific
  surface.

### Deliberate omissions

- No tool lets the caller supply or override raw provider API calls.
- No tool returns or accepts credentials.
- Zone-level operations (creating/deleting whole zones, nameserver delegation)
  are out of scope here by design — this is record CRUD only.

## Configuration (environment, resolved at call time)

| Variable | Effect |
| --- | --- |
| `DNS_PROVIDER` | Which provider adapter the production backend uses (consumed once infra wires it). |
| `DNS_ZONE` | The default zone the backend operates on. |

No credentials are read from the environment by this server; identity is
resolved per call from the runtime (WIF/GSM) by the credential provider.

## What infra must wire (the build split)

This repo is the **offline-testable scaffold**. The core, the MCP wrapper, the
backend *interface*, and a full offline test suite (fake in-memory backend) are
complete here. The **live provider adapter + credentials are intentionally not
wired** — that is the infra half:

1. **Backend implementation.** Add a real adapter implementing the `DnsBackend`
   protocol in `src/dns_mcp/backend.py` (the default `UnconfiguredBackend`
   currently fails fast with "not wired up"). It must map the provider-neutral
   `Record` model to and from the provider's record API:
   - `list_records` / `get_record` read the zone's records.
   - `create_record` / `update_record` / `delete_record` mutate them, honouring
     conflict (create) and missing-record (get/update/delete) semantics.
   - A likely first adapter is **Porkbun** (`speedforge.dev` is registered there)
     or **Google Cloud DNS**.
2. **Zone.** The DNS zone(s) the adapter operates on (`DNS_ZONE`).
3. **Identity (keyless).** Prefer a **Workload Identity Federation** service
   account for Cloud DNS, or a GSM-backed API key for an API-key provider like
   Porkbun. The `CredentialProvider` resolves this at call time; **no key is
   stored in this repo or the image**.
4. **Config.** Set `DNS_PROVIDER` and `DNS_ZONE` for the workload.

Until step 1 lands, the production backend raises and only the fake-backed
offline path runs — so this scaffold is safe to ship and CI is green without any
provider access.

## Install

Run directly from GitHub with the MCP extra:

```bash
uvx --from "dns-mcp[mcp] @ git+https://github.com/selamy-labs/dns-mcp@v0.1.0" dns-mcp
```

Or with pipx:

```bash
pipx install "dns-mcp[mcp] @ git+https://github.com/selamy-labs/dns-mcp@v0.1.0"
```

## MCP client config

```json
{
  "mcpServers": {
    "dns": {
      "command": "uvx",
      "args": [
        "--from",
        "dns-mcp[mcp] @ git+https://github.com/selamy-labs/dns-mcp@v0.1.0",
        "dns-mcp"
      ],
      "env": {
        "DNS_PROVIDER": "porkbun",
        "DNS_ZONE": "example.com"
      }
    }
  }
}
```

## Architecture

The DNS record logic lives once in `dns_mcp.core.DnsService`; the MCP server in
`dns_mcp.mcp_server` is a thin wrapper that serialises structured results to JSON
and maps expected failures to `ToolError`. All record access goes through an
**injected backend** (`dns_mcp.backend.DnsBackend`) and all credential resolution
through an **injected `CredentialProvider`**, so the full validate / route / shape
path is exercised offline in tests with a fake in-memory backend — no provider, no
network. The default backend (`UnconfiguredBackend`) uses only the standard
library until infra wires a real adapter, so the core package has zero runtime
dependencies; the `mcp` SDK is an optional extra.

## Development

```bash
python -m pip install -e ".[test]"
ruff format --check .
ruff check .
coverage run -m pytest
coverage report --fail-under=95
```

## License

MIT — see [LICENSE](LICENSE).
