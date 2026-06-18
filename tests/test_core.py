"""Tests for the DNS core, fully offline against a fake in-memory backend.

These exercise validation/normalisation, routing to the backend, the record-type
allowlist, TTL bounds, CRUD semantics (conflict on create, missing on
get/update/delete), error mapping, and the credential contract (resolved per
call, passed to the backend, never in the output).
"""

from __future__ import annotations

import pytest

from dns_mcp.core import (
    MAX_TTL,
    MIN_TTL,
    DnsError,
    DnsService,
    Record,
    RecordKey,
)
from tests.conftest import FakeBackend, FakeClock, RecordingCredentialProvider
from tests.fixtures import ZONE, backend


def _service(fake: FakeBackend | None = None, creds: RecordingCredentialProvider | None = None) -> DnsService:
    return DnsService(fake or backend(), credentials=creds or RecordingCredentialProvider(), clock=FakeClock())


def test_list_records_round_trip_and_sorted() -> None:
    out = _service().list_records(ZONE)
    assert out["zone"] == ZONE
    assert out["count"] == 5
    keys = [(r["name"], r["type"]) for r in out["records"]]
    assert keys == sorted(keys)


def test_list_records_normalises_zone_case_and_trailing_dot() -> None:
    out = _service().list_records("Example.com.")
    assert out["zone"] == ZONE


def test_get_record_round_trip() -> None:
    out = _service().get_record(ZONE, "www", "CNAME")
    assert out["record"]["type"] == "CNAME"
    assert out["record"]["value"] == "example.com"


def test_get_record_normalises_type_case() -> None:
    out = _service().get_record(ZONE, "www", "cname")
    assert out["record"]["type"] == "CNAME"


def test_create_record_round_trip() -> None:
    fake = backend()
    out = _service(fake).create_record(ZONE, "api", "A", "203.0.113.30", ttl=120)
    assert out["created"] is True
    assert out["record"]["name"] == "api"
    assert out["record"]["ttl"] == 120
    # The record is now retrievable from the same backend.
    assert _service(fake).get_record(ZONE, "api", "A")["record"]["value"] == "203.0.113.30"


def test_create_record_conflict_raises() -> None:
    with pytest.raises(DnsError, match="already exists"):
        _service().create_record(ZONE, "www", "CNAME", "elsewhere.example.com")


def test_create_record_carries_priority() -> None:
    fake = backend()
    _service(fake).create_record(ZONE, "alt", "MX", "mail2.example.com", ttl=3600, priority=20)
    call = next(c for c in fake.calls if c["op"] == "create_record")
    assert call["record"].priority == 20


def test_update_record_round_trip() -> None:
    fake = backend()
    out = _service(fake).update_record(ZONE, "@", "A", "198.51.100.5", ttl=600)
    assert out["updated"] is True
    assert out["record"]["value"] == "198.51.100.5"
    assert _service(fake).get_record(ZONE, "@", "A")["record"]["ttl"] == 600


def test_update_missing_record_raises() -> None:
    with pytest.raises(DnsError, match="no A record"):
        _service().update_record(ZONE, "ghost", "A", "203.0.113.99")


def test_delete_record_round_trip() -> None:
    fake = backend()
    out = _service(fake).delete_record(ZONE, "www", "CNAME")
    assert out["deleted"] == {"name": "www", "type": "CNAME"}
    with pytest.raises(DnsError, match="no CNAME record"):
        _service(fake).get_record(ZONE, "www", "CNAME")


def test_delete_missing_record_raises() -> None:
    with pytest.raises(DnsError, match="no TXT record"):
        _service().delete_record(ZONE, "ghost", "TXT")


@pytest.mark.parametrize("rr_type", ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA", "ALIAS", "PTR"])
def test_create_accepts_each_supported_type(rr_type: str) -> None:
    fake = backend()
    out = _service(fake).create_record(ZONE, "rec", rr_type, "data-value")
    assert out["record"]["type"] == rr_type


def test_unsupported_type_rejected() -> None:
    with pytest.raises(DnsError, match="unsupported record type"):
        _service().create_record(ZONE, "rec", "SPF", "v=spf1 -all")


def test_backend_failure_maps_to_dns_error() -> None:
    svc = _service(backend(fail_with="auth: api key expired"))
    with pytest.raises(DnsError, match="auth: api key expired"):
        svc.list_records(ZONE)


@pytest.mark.parametrize("zone", ["", "  ", "not a domain", "../etc", "a;b.com", "localhost"])
def test_invalid_zone_rejected(zone: str) -> None:
    with pytest.raises(DnsError):
        _service().list_records(zone)


@pytest.mark.parametrize("name", ["", "  ", "bad name", "../etc", "a;b"])
def test_invalid_record_name_rejected(name: str) -> None:
    with pytest.raises(DnsError):
        _service().get_record(ZONE, name, "A")


@pytest.mark.parametrize("name", ["@", "*", "www", "a.b.c", "*.dev", "_dmarc"])
def test_valid_record_names_accepted(name: str) -> None:
    # NS is unused by the fixture zone, so none of these collide on create.
    fake = backend()
    out = _service(fake).create_record(ZONE, name, "NS", "ns1.example.net")
    assert out["record"]["name"] == name


def test_empty_value_rejected() -> None:
    with pytest.raises(DnsError, match="value must not be empty"):
        _service().create_record(ZONE, "rec", "A", "   ")


def test_overlong_value_rejected() -> None:
    with pytest.raises(DnsError, match="value too long"):
        _service().create_record(ZONE, "rec", "TXT", "x" * 5000)


def test_ttl_below_floor_rejected() -> None:
    with pytest.raises(DnsError, match="ttl must be"):
        _service().create_record(ZONE, "rec", "A", "203.0.113.1", ttl=MIN_TTL - 1)


def test_ttl_above_ceiling_rejected() -> None:
    with pytest.raises(DnsError, match="ttl too large"):
        _service().create_record(ZONE, "rec", "A", "203.0.113.1", ttl=MAX_TTL + 1)


def test_ttl_none_left_for_provider_default() -> None:
    fake = backend()
    out = _service(fake).create_record(ZONE, "rec", "A", "203.0.113.1")
    assert out["record"]["ttl"] is None


def test_credentials_resolved_per_call_and_passed_to_backend() -> None:
    creds = RecordingCredentialProvider(sentinel="SECRET-TOKEN")
    fake = backend()
    svc = _service(fake, creds)
    svc.list_records(ZONE)
    svc.get_record(ZONE, "www", "CNAME")
    assert creds.resolved == 2
    assert all(call["credentials"] == "SECRET-TOKEN" for call in fake.calls)


def test_credentials_never_appear_in_output() -> None:
    creds = RecordingCredentialProvider(sentinel="SECRET-TOKEN")
    out = _service(backend(), creds).list_records(ZONE)
    assert "SECRET-TOKEN" not in repr(out)


def test_default_credential_provider_returns_none() -> None:
    from dns_mcp.core import EnvCredentialProvider

    assert EnvCredentialProvider().resolve() is None
    # A service with no explicit provider still works (ambient identity / None).
    out = DnsService(backend()).list_records(ZONE)
    assert out["count"] == 5


def test_record_to_public_shape() -> None:
    record = Record(name="@", type="MX", value="mail.example.com", ttl=3600, priority=10)
    public = record.to_public()
    assert public == {"name": "@", "type": "MX", "value": "mail.example.com", "ttl": 3600, "priority": 10}


def test_record_key_derived_from_record() -> None:
    record = Record(name="www", type="A", value="203.0.113.1")
    assert record.key == RecordKey(name="www", type="A")
    assert record.key.to_public() == {"name": "www", "type": "A"}
