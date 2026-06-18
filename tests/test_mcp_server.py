"""Tests for the thin MCP wrapper, fully offline.

The server module is a thin adapter over :class:`dns_mcp.core.DnsService`: it
builds the service, maps :class:`DnsError` to ``ToolError``, and registers the
tools. Tests inject a fake-backend-backed service via ``set_service`` so nothing
touches a DNS provider, and assert the wrapper's mapping and registration.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from dns_mcp import mcp_server
from dns_mcp.core import DnsService
from tests.conftest import FakeClock, RecordingCredentialProvider
from tests.fixtures import ZONE, backend


@pytest.fixture(autouse=True)
def _reset_service() -> None:
    mcp_server.set_service(None)
    yield
    mcp_server.set_service(None)


def _install_fake(fail_with: str | None = None) -> None:
    service = DnsService(backend(fail_with=fail_with), credentials=RecordingCredentialProvider(), clock=FakeClock())
    mcp_server.set_service(service)


def test_list_records_tool_round_trip() -> None:
    _install_fake()
    out = mcp_server.dns_list_records(ZONE)
    assert out["count"] == 5


def test_get_record_tool_round_trip() -> None:
    _install_fake()
    out = mcp_server.dns_get_record(ZONE, "www", "CNAME")
    assert out["record"]["value"] == "example.com"


def test_create_record_tool_round_trip() -> None:
    _install_fake()
    out = mcp_server.dns_create_record(ZONE, "api", "A", "203.0.113.30", ttl=120)
    assert out["created"] is True
    assert out["record"]["ttl"] == 120


def test_create_record_tool_defaults_optional_args() -> None:
    _install_fake()
    out = mcp_server.dns_create_record(ZONE, "api", "A", "203.0.113.30")
    assert out["record"]["ttl"] is None
    assert out["record"]["priority"] is None


def test_update_record_tool_round_trip() -> None:
    _install_fake()
    out = mcp_server.dns_update_record(ZONE, "@", "A", "198.51.100.5", ttl=600)
    assert out["updated"] is True
    assert out["record"]["value"] == "198.51.100.5"


def test_delete_record_tool_round_trip() -> None:
    _install_fake()
    out = mcp_server.dns_delete_record(ZONE, "www", "CNAME")
    assert out["deleted"]["type"] == "CNAME"


def test_unknown_record_maps_to_tool_error() -> None:
    _install_fake()
    with pytest.raises(ToolError, match="no A record"):
        mcp_server.dns_get_record(ZONE, "ghost", "A")


def test_unsupported_type_maps_to_tool_error() -> None:
    _install_fake()
    with pytest.raises(ToolError, match="unsupported record type"):
        mcp_server.dns_create_record(ZONE, "rec", "SPF", "v=spf1 -all")


def test_backend_failure_maps_to_tool_error() -> None:
    _install_fake(fail_with="auth: api key expired")
    with pytest.raises(ToolError, match="api key expired"):
        mcp_server.dns_list_records(ZONE)


def test_build_server_registers_all_tools() -> None:
    _install_fake()
    server = mcp_server.build_server()
    assert server.name == "dns-mcp"
    names = {tool.name for tool in server._tool_manager.list_tools()}
    assert {
        "dns_list_records",
        "dns_get_record",
        "dns_create_record",
        "dns_update_record",
        "dns_delete_record",
    } <= names


def test_service_is_built_from_env_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # No service pre-installed -> the server builds one with the (not-wired)
    # backend, which fails fast. No provider/network is touched because the
    # backend raises before any client is created.
    monkeypatch.setenv("DNS_PROVIDER", "porkbun")
    monkeypatch.setenv("DNS_ZONE", "example.com")
    mcp_server.set_service(None)
    with pytest.raises(ToolError, match="not wired up"):
        mcp_server.dns_list_records("example.com")


def test_main_runs_the_built_server(monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[bool] = []
    monkeypatch.setattr(FastMCP, "run", lambda self, *a, **k: ran.append(True))
    mcp_server.main()
    assert ran == [True]
