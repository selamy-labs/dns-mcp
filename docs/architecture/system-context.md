# System context

`dns-mcp` exposes five typed DNS record CRUD tools to an MCP client. The MCP
wrapper delegates validation, normalization, per-call credential resolution,
and backend routing to `DnsService`. Provider-specific behavior is isolated
behind the injected `DnsBackend` protocol.

```mermaid
flowchart LR
    client["MCP client"]

    subgraph package["dns-mcp package"]
        server["FastMCP server<br/>five typed record tools"]
        service["DnsService<br/>validation and routing"]
        backend["DnsBackend protocol<br/>record CRUD boundary"]
        unconfigured["UnconfiguredBackend<br/>current default: fails fast"]

        server -->|"structured calls"| service
        service -->|"credentials passed per call"| backend
        backend -->|"current default"| unconfigured
    end

    identity["Ambient runtime identity<br/>no credential stored by dns-mcp"]
    adapter["Production provider adapter<br/>not implemented in this repository"]
    provider["DNS provider API<br/>external system"]

    client -->|"MCP over stdio"| server
    identity -->|"resolved at call time"| service
    backend -.->|"future infra injection"| adapter
    adapter -.->|"provider record API"| provider
```

The current default path reaches `UnconfiguredBackend`, which rejects every DNS
operation until infrastructure supplies a real provider adapter. Tests inject an
in-memory backend and exercise the full service path without provider access or
network I/O.

This diagram is hand-maintained because the repository has no manifest that
describes the MCP, service, and backend relationships. Its sources of truth are
[`mcp_server.py`](../../src/dns_mcp/mcp_server.py),
[`core.py`](../../src/dns_mcp/core.py), and
[`backend.py`](../../src/dns_mcp/backend.py).
