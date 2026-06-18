"""Shared offline test doubles: a fake in-memory DNS backend, clock, and credentials.

Nothing in the test suite touches a DNS provider or the network. The fake
backend serves records from an in-memory zone, records every call it received
(including the credentials it was handed), enforces create-conflict and
missing-record semantics, and can be told to raise so the error path is
exercised. The fake clock yields deterministic timestamps. The recording
credential provider lets tests assert credentials are resolved per call and
never leak into returned payloads.
"""

from __future__ import annotations

from typing import Any

from dns_mcp.backend import BackendError
from dns_mcp.core import Record, RecordKey


class FakeBackend:
    """An in-memory DNS backend driven by a canned set of records for one zone.

    Serves whatever ``records`` it is built with (keyed by ``(name, type)``);
    records each call so tests can assert routing and that credentials were
    passed through. ``create`` rejects an existing key, ``get`` / ``update`` /
    ``delete`` reject a missing one. Set ``fail_with`` to make every call raise a
    :class:`BackendError` (the auth/provider-failure path).
    """

    def __init__(
        self,
        records: list[Record] | None = None,
        fail_with: str | None = None,
    ) -> None:
        self._records: dict[tuple[str, str], Record] = {}
        for record in records or []:
            self._records[(record.name, record.type)] = record
        self._fail_with = fail_with
        self.calls: list[dict[str, Any]] = []

    def _record(self, **call: Any) -> None:
        self.calls.append(call)

    def _guard(self) -> None:
        if self._fail_with is not None:
            raise BackendError(self._fail_with)

    def list_records(self, zone: str, *, credentials: Any) -> list[Record]:
        self._record(op="list_records", zone=zone, credentials=credentials)
        self._guard()
        return list(self._records.values())

    def get_record(self, zone: str, key: RecordKey, *, credentials: Any) -> Record:
        self._record(op="get_record", zone=zone, key=key, credentials=credentials)
        self._guard()
        record = self._records.get((key.name, key.type))
        if record is None:
            raise BackendError(f"no {key.type} record named {key.name!r} in {zone}")
        return record

    def create_record(self, zone: str, record: Record, *, credentials: Any) -> Record:
        self._record(op="create_record", zone=zone, record=record, credentials=credentials)
        self._guard()
        if (record.name, record.type) in self._records:
            raise BackendError(f"{record.type} record named {record.name!r} already exists in {zone}")
        self._records[(record.name, record.type)] = record
        return record

    def update_record(self, zone: str, record: Record, *, credentials: Any) -> Record:
        self._record(op="update_record", zone=zone, record=record, credentials=credentials)
        self._guard()
        if (record.name, record.type) not in self._records:
            raise BackendError(f"no {record.type} record named {record.name!r} in {zone}")
        self._records[(record.name, record.type)] = record
        return record

    def delete_record(self, zone: str, key: RecordKey, *, credentials: Any) -> RecordKey:
        self._record(op="delete_record", zone=zone, key=key, credentials=credentials)
        self._guard()
        if (key.name, key.type) not in self._records:
            raise BackendError(f"no {key.type} record named {key.name!r} in {zone}")
        del self._records[(key.name, key.type)]
        return key


class FakeClock:
    """A deterministic clock: fixed-format ISO time and a counting monotonic."""

    def __init__(self) -> None:
        self._seq = 0

    def now_iso(self) -> str:
        self._seq += 1
        return f"2026-06-17T00:00:{self._seq:02d}Z"

    def monotonic_ns(self) -> int:
        self._seq += 1
        return self._seq


class RecordingCredentialProvider:
    """A credential provider that hands out a sentinel and counts resolutions.

    Lets tests assert credentials are resolved *per call* and that the sentinel
    is passed to the backend but never appears in a returned payload.
    """

    def __init__(self, sentinel: Any = "FAKE-CREDS") -> None:
        self.sentinel = sentinel
        self.resolved = 0

    def resolve(self) -> Any:
        self.resolved += 1
        return self.sentinel
