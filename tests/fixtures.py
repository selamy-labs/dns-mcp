"""Canned DNS records the fake backend serves offline.

A minimal, hand-built zone shaped like a generic domain (a handful of records
across several RR types). It is not copied from any live zone and contains no
credentials. The point is to prove the server is provider-agnostic: the core
knows none of these names ahead of time and no provider is contacted.
"""

from __future__ import annotations

from dns_mcp.core import Record
from tests.conftest import FakeBackend

ZONE = "example.com"


def records() -> list[Record]:
    """A spread of record types so CRUD, MX priority, and wildcards are observable."""
    return [
        Record(name="@", type="A", value="203.0.113.10", ttl=300),
        Record(name="www", type="CNAME", value="example.com", ttl=3600),
        Record(name="@", type="MX", value="mail.example.com", ttl=3600, priority=10),
        Record(name="@", type="TXT", value="v=spf1 -all", ttl=3600),
        Record(name="*", type="A", value="203.0.113.20", ttl=300),
    ]


def backend(fail_with: str | None = None) -> FakeBackend:
    """The fake backend most tests operate against."""
    return FakeBackend(records=records(), fail_with=fail_with)
