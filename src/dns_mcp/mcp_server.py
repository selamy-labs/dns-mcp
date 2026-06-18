"""MCP server exposing provider-agnostic DNS record CRUD as typed tools.

This is an optional integration: install it with ``pip install dns-mcp[mcp]``.
The core package keeps its runtime dependencies minimal (stdlib only); the
``mcp`` SDK is required only to run this server.

Every tool is a thin wrapper over :class:`dns_mcp.core.DnsService`, so
validation, normalisation, credential resolution, and backend routing live in
exactly one place. Tools take structured inputs and return JSON objects.
Expected failures (unknown zone/record, unsupported type, conflict, backend/auth
error) surface as ``ToolError`` with a clean message.

Backend selection (deliberate)
------------------------------
By default this server constructs :class:`UnconfiguredBackend`, which is **not
live-wired**: no DNS provider is implemented here, so it fails fast until infra
completes a real adapter (Porkbun, Cloud DNS, ...). Tests (and any offline use)
inject a fake in-memory backend via :func:`set_service`, so nothing here touches
a provider or the network unless infra has wired the real adapter.

Configuration is resolved at call time from the environment: ``DNS_PROVIDER`` and
``DNS_ZONE`` (consumed by the real backend once infra wires it). No credential is
ever read or stored here; identity is resolved per call from WIF/GSM by the
credential provider.
"""

from __future__ import annotations

import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.fastmcp.exceptions import ToolError
except ModuleNotFoundError as error:  # pragma: no cover - import guard
    raise SystemExit(
        "dns-mcp server requires the 'mcp' package. Install it with: pip install 'dns-mcp[mcp]'"
    ) from error

from dns_mcp.backend import UnconfiguredBackend
from dns_mcp.core import DnsError, DnsService

INSTRUCTIONS = (
    "Provider-agnostic DNS record CRUD over a configurable backend. Use "
    "dns_list_records(zone) to enumerate a zone's records, dns_get_record(zone, "
    "name, type) to read one, and dns_create_record / dns_update_record / "
    "dns_delete_record to mutate them. A record is identified by its name and "
    "type (A/AAAA/CNAME/TXT/MX/...); 'name' may be '@' for the apex or a wildcard "
    "'*'. The DNS provider is supplied by the backend (not hard-coded). "
    "Credentials are resolved at call time from the runtime identity and never "
    "accepted or returned."
)

# A single service per process. The backend and config are resolved once at
# build time from the environment; credentials are never read or stored here.
_SERVICE: DnsService | None = None


def _build_service() -> DnsService:
    """Construct the service from environment config. Separated so tests can
    inject a fake-backed service instead."""
    backend = UnconfiguredBackend(
        provider=os.environ.get("DNS_PROVIDER"),
        zone=os.environ.get("DNS_ZONE"),
    )
    return DnsService(backend)


def set_service(service: DnsService | None) -> None:
    """Install the service the tools use (tests inject a fake-backed one)."""
    global _SERVICE
    _SERVICE = service


def _service() -> DnsService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = _build_service()
    return _SERVICE


def _run(call: Any) -> dict[str, Any]:
    """Execute a service call, mapping expected failures to ``ToolError``."""
    try:
        return call()
    except DnsError as error:
        raise ToolError(str(error)) from error


def dns_list_records(zone: str) -> dict[str, Any]:
    """List every DNS record in ``zone`` (sorted by name then type)."""
    service = _service()
    return _run(lambda: service.list_records(zone))


def dns_get_record(zone: str, name: str, type: str) -> dict[str, Any]:
    """Get the record identified by ``name`` + ``type`` in ``zone``."""
    service = _service()
    return _run(lambda: service.get_record(zone, name, type))


def dns_create_record(
    zone: str,
    name: str,
    type: str,
    value: str,
    ttl: int | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Create a DNS record in ``zone``.

    ``name`` is the record name (``@`` for the apex, ``*`` for a wildcard).
    ``type`` is a supported RR type (A/AAAA/CNAME/TXT/MX/...). ``value`` is the
    record data. ``ttl`` (seconds) is optional; ``priority`` is used by MX/SRV.
    The backend rejects a record that already exists.
    """
    service = _service()
    return _run(lambda: service.create_record(zone, name, type, value, ttl=ttl, priority=priority))


def dns_update_record(
    zone: str,
    name: str,
    type: str,
    value: str,
    ttl: int | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """Update the record identified by ``name`` + ``type`` in ``zone`` to ``value``
    (and optional ``ttl`` / ``priority``)."""
    service = _service()
    return _run(lambda: service.update_record(zone, name, type, value, ttl=ttl, priority=priority))


def dns_delete_record(zone: str, name: str, type: str) -> dict[str, Any]:
    """Delete the record identified by ``name`` + ``type`` in ``zone``."""
    service = _service()
    return _run(lambda: service.delete_record(zone, name, type))


TOOLS = (
    dns_list_records,
    dns_get_record,
    dns_create_record,
    dns_update_record,
    dns_delete_record,
)


def build_server() -> FastMCP:
    """Build the dns-mcp server with every DNS tool registered."""
    server = FastMCP("dns-mcp", instructions=INSTRUCTIONS)
    for tool in TOOLS:
        server.add_tool(tool)
    return server


def main() -> None:
    """Run the dns-mcp server over stdio."""
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
