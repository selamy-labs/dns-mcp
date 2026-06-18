"""DNS backend abstraction so the core stays provider-agnostic and offline-testable.

The DNS core never talks to a provider's API directly. It depends only on the
:class:`DnsBackend` protocol below, which serves record CRUD against a single
zone/domain and returns structured results. Tests inject a fake in-memory
backend, so the full validate/route/shape path is exercised offline with no
provider and no network.

Production injects a real adapter (e.g. Porkbun or Google Cloud DNS), a thin
wrapper over the provider's record API. **No real adapter is live-wired in this
repo** -- :class:`UnconfiguredBackend` is the documented integration point that
infra completes (provider client, zone resolution, and keyless WIF / GSM-backed
credentials). See the README "What infra must wire" section.

Two properties live here and nowhere else:

* **Provider-agnostic.** The backend exposes only record CRUD over a validated
  record model. There is no provider-specific surface in the core; swapping
  Porkbun for Cloud DNS is a backend change, nothing else moves.
* **No embedded credentials.** The backend carries no token. Credentials are
  passed in *per call* (resolved by the core's :class:`CredentialProvider` from
  WIF/GSM/env) or left ``None`` to use ambient identity; nothing is stored.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from dns_mcp.core import Record, RecordKey


class BackendError(Exception):
    """A backend failed to serve a request for an expected reason.

    Covers missing/unknown zone or record, auth failure, conflict (a record that
    already exists), and provider API failure. The core maps it to
    :class:`dns_mcp.core.DnsError`.
    """


class DnsBackend(Protocol):
    """Serves DNS record CRUD for a zone. Credentials are passed per call, never stored.

    Implementations wrap a single provider (Porkbun, Cloud DNS, ...) and translate
    the provider-neutral record model below to and from the provider's API. A
    provider/auth failure is raised as :class:`BackendError`.
    """

    def list_records(self, zone: str, *, credentials: Any) -> list[Record]: ...

    def get_record(self, zone: str, key: RecordKey, *, credentials: Any) -> Record: ...

    def create_record(self, zone: str, record: Record, *, credentials: Any) -> Record: ...

    def update_record(self, zone: str, record: Record, *, credentials: Any) -> Record: ...

    def delete_record(self, zone: str, key: RecordKey, *, credentials: Any) -> RecordKey: ...


class UnconfiguredBackend:
    """Default backend: a documented, **not live-wired** provider integration point.

    **No provider is wired here.** This class documents where the real adapter
    plugs in and fails fast until infra completes it (see the README). The
    intended shape of a real adapter:

    * Constructed with a ``provider`` selector and a ``zone`` (the apex domain the
      records belong to); the record name/type identify a record within that zone.
    * ``credentials`` is resolved *at call time* by the core's
      :class:`dns_mcp.core.CredentialProvider` from Workload Identity Federation
      (keyless) or a GSM-backed API key, and handed to the provider client per
      request; no key is read or stored by this module.
    * Every method maps the provider-neutral :class:`dns_mcp.core.Record` to and
      from the provider's own record representation; nothing in the core knows
      the provider's wire format.

    Wiring it up is the infra half of this build split; until then this backend
    raises so the offline/fake path is the only one exercised by tests.
    """

    def __init__(self, provider: str | None = None, zone: str | None = None) -> None:
        self._provider = provider
        self._zone = zone

    def _not_wired(self) -> BackendError:
        return BackendError(
            "No DNS provider backend is wired up in this repo. Infra must supply a "
            "provider adapter (e.g. Porkbun or Cloud DNS), the zone, and keyless "
            "WIF / GSM-backed credentials, and implement the record CRUD path (see "
            "README); until then this backend is not wired up."
        )

    def list_records(self, zone: str, *, credentials: Any) -> list[Record]:
        raise self._not_wired()

    def get_record(self, zone: str, key: RecordKey, *, credentials: Any) -> Record:
        raise self._not_wired()

    def create_record(self, zone: str, record: Record, *, credentials: Any) -> Record:
        raise self._not_wired()

    def update_record(self, zone: str, record: Record, *, credentials: Any) -> Record:
        raise self._not_wired()

    def delete_record(self, zone: str, key: RecordKey, *, credentials: Any) -> RecordKey:
        raise self._not_wired()


class Clock(Protocol):
    """A wall clock, injected so timestamps are testable."""

    def now_iso(self) -> str: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    """The real clock: UTC ISO timestamps and a monotonic nanosecond counter."""

    def now_iso(self) -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()
