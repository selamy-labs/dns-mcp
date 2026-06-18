"""Provider-agnostic DNS record CRUD core.

This module holds the DNS record logic exactly once. The MCP server in
:mod:`dns_mcp.mcp_server` is a thin wrapper that serialises these structured
results to JSON; nothing here imports the MCP SDK.

The capability exposed is narrow on purpose: *list / get / create / update /
delete* DNS records in a single zone through a configurable backend. There is no
provider-specific surface and no free-form execution path -- a caller supplies a
validated record (name/type/value/ttl), never raw provider API text.

Provider-agnostic by design
--------------------------
This package does **not** hard-code any provider. A zone's records are served
entirely by the injected :class:`DnsBackend`, so the same server works against
Porkbun, Cloud DNS, or whatever infra wires up (see :mod:`dns_mcp.backend`). The
core only validates, normalises, and structures requests; it never assumes a
particular provider's wire format.

Security model
--------------
* **Bounded surface.** The tools are list/get/create/update/delete over a
  validated record model. There is no raw provider-call escape hatch; a caller
  cannot supply arbitrary API text.
* **No embedded credentials.** Nothing here stores a token or key. Credentials
  are resolved at *call time* from an injected :class:`CredentialProvider`
  (backed by WIF/GSM/env in production) and handed to the backend; they never
  live in this module, in source, or in returned payloads.
* **Validated inputs.** Zone, record name, type, and TTL are restricted to a
  conservative shape so a rejected request can never smuggle injection into the
  backend. Record types are an allowlist of the common DNS RR types.

All record access goes through the injected :class:`DnsBackend`, and all timing
through the injected :class:`Clock`, so the full validate/route/shape path is
exercised offline in tests with a fake in-memory backend -- no provider, no
network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from dns_mcp.backend import BackendError, Clock, DnsBackend, SystemClock

# Supported DNS record types. Kept as a small, closed allowlist so a caller
# cannot pass an arbitrary type string through to the backend.
SUPPORTED_TYPES = frozenset({"A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA", "ALIAS", "PTR"})

# Bounds on a record TTL (seconds). The floor is the common provider minimum; the
# ceiling stops an absurd value. ``None`` means "let the provider default it".
MIN_TTL = 60
MAX_TTL = 2_147_483_647

# A zone (apex domain) and a record name are restricted to a conservative DNS
# label/hostname shape so a rejected lookup can never smuggle injection or
# traversal into the backend. ``@`` is allowed as the apex shorthand and ``*`` as
# a wildcard label.
_ZONE_RE = re.compile(r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$")
# A record name is the apex (``@``), a bare wildcard (``*``), or a host
# label/FQDN optionally prefixed with a ``*.`` wildcard. Underscore is allowed so
# names like ``_dmarc`` and ``_acme-challenge`` are accepted.
_LABEL = r"[A-Za-z0-9_]([A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?"
_NAME_RE = re.compile(rf"^(@|\*|(\*\.)?{_LABEL}(\.{_LABEL})*)$")

# A record value is opaque text (an IP, a hostname, a TXT body, ...). We only
# bound its length here; the backend/provider validates it against the type.
MAX_VALUE_LEN = 4096


class DnsError(Exception):
    """A DNS request failed for an expected, user-facing reason.

    The MCP layer maps this to a ``ToolError`` so clients get a clean message
    instead of a stack trace.
    """


def _validate_zone(zone: str) -> str:
    cleaned = zone.strip().rstrip(".").lower()
    if not cleaned:
        raise DnsError("zone must not be empty")
    if not _ZONE_RE.match(cleaned):
        raise DnsError(f"invalid zone {zone!r}: must be a dotted apex domain (e.g. example.com)")
    return cleaned


def _validate_name(name: str) -> str:
    cleaned = name.strip().rstrip(".")
    if not cleaned:
        raise DnsError("record name must not be empty (use '@' for the zone apex)")
    if not _NAME_RE.match(cleaned):
        raise DnsError(f"invalid record name {name!r}: must be a host label, FQDN, '@', or wildcard")
    return cleaned


def _validate_type(rr_type: str) -> str:
    cleaned = rr_type.strip().upper()
    if cleaned not in SUPPORTED_TYPES:
        raise DnsError(f"unsupported record type {rr_type!r}: choose one of {sorted(SUPPORTED_TYPES)}")
    return cleaned


def _validate_value(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise DnsError("record value must not be empty")
    if len(cleaned) > MAX_VALUE_LEN:
        raise DnsError(f"record value too long: {len(cleaned)} > {MAX_VALUE_LEN}")
    return cleaned


def _validate_ttl(ttl: int | None) -> int | None:
    if ttl is None:
        return None
    if ttl < MIN_TTL:
        raise DnsError(f"ttl must be >= {MIN_TTL}")
    if ttl > MAX_TTL:
        raise DnsError(f"ttl too large: {ttl} > {MAX_TTL}")
    return ttl


@dataclass(frozen=True)
class RecordKey:
    """The identity of a record within a zone: its name and type.

    A zone may hold several records sharing a name+type (e.g. multiple ``A``
    answers); ``get`` / ``delete`` operate on that name+type set, which is how
    every provider's record API is keyed.
    """

    name: str
    type: str

    def to_public(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type}


@dataclass(frozen=True)
class Record:
    """One DNS record in provider-neutral form.

    ``ttl`` is optional (``None`` lets the provider default it). ``priority`` is
    used by types that need it (``MX``, ``SRV``); it is carried verbatim and the
    backend applies it where the provider requires it.
    """

    name: str
    type: str
    value: str
    ttl: int | None = None
    priority: int | None = None

    @property
    def key(self) -> RecordKey:
        return RecordKey(name=self.name, type=self.type)

    def to_public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "ttl": self.ttl,
            "priority": self.priority,
        }


class CredentialProvider(Protocol):
    """Resolves backend credentials at call time; never stores them in the core.

    Production implementations resolve from Workload Identity Federation / GSM /
    the process environment when a call runs. The returned object is opaque to
    the core -- it is handed straight to the backend and never logged, returned,
    or persisted.
    """

    def resolve(self) -> Any: ...


class EnvCredentialProvider:
    """Default provider: defers entirely to the backend's own ambient auth.

    Returns ``None`` so the backend uses its environment-resolved identity (e.g.
    Application Default Credentials / WIF). It deliberately reads and stores no
    secret value itself -- there is nothing here to leak.
    """

    def resolve(self) -> Any:
        return None


class DnsService:
    """Serves provider-agnostic DNS record CRUD from an injected backend.

    Every method validates and normalises its inputs, resolves credentials at
    call time via the injected :class:`CredentialProvider`, routes the request to
    the injected :class:`DnsBackend`, and returns a structured, credential-free
    result. The service holds no provider knowledge of its own.
    """

    def __init__(
        self,
        backend: DnsBackend,
        *,
        credentials: CredentialProvider | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._backend = backend
        self._credentials = credentials or EnvCredentialProvider()
        self._clock = clock or SystemClock()

    def list_records(self, zone: str) -> dict[str, Any]:
        """List every record in ``zone``, sorted by (name, type)."""
        z = _validate_zone(zone)
        records = self._call(lambda creds: self._backend.list_records(z, credentials=creds))
        public = [record.to_public() for record in records]
        public.sort(key=lambda item: (item["name"], item["type"]))
        return {"zone": z, "count": len(public), "records": public}

    def get_record(self, zone: str, name: str, rr_type: str) -> dict[str, Any]:
        """Get the record identified by ``name`` + ``rr_type`` in ``zone``."""
        z = _validate_zone(zone)
        key = RecordKey(name=_validate_name(name), type=_validate_type(rr_type))
        record = self._call(lambda creds: self._backend.get_record(z, key, credentials=creds))
        return {"zone": z, "record": record.to_public()}

    def create_record(
        self,
        zone: str,
        name: str,
        rr_type: str,
        value: str,
        ttl: int | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Create a record in ``zone``. The backend rejects a conflicting record."""
        record = self._build_record(zone, name, rr_type, value, ttl, priority)
        z = _validate_zone(zone)
        created = self._call(lambda creds: self._backend.create_record(z, record, credentials=creds))
        return {"zone": z, "record": created.to_public(), "created": True}

    def update_record(
        self,
        zone: str,
        name: str,
        rr_type: str,
        value: str,
        ttl: int | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Update the record identified by ``name`` + ``rr_type`` in ``zone``."""
        record = self._build_record(zone, name, rr_type, value, ttl, priority)
        z = _validate_zone(zone)
        updated = self._call(lambda creds: self._backend.update_record(z, record, credentials=creds))
        return {"zone": z, "record": updated.to_public(), "updated": True}

    def delete_record(self, zone: str, name: str, rr_type: str) -> dict[str, Any]:
        """Delete the record identified by ``name`` + ``rr_type`` in ``zone``."""
        z = _validate_zone(zone)
        key = RecordKey(name=_validate_name(name), type=_validate_type(rr_type))
        removed = self._call(lambda creds: self._backend.delete_record(z, key, credentials=creds))
        return {"zone": z, "deleted": removed.to_public()}

    # -- internals -------------------------------------------------------------

    def _build_record(
        self,
        zone: str,
        name: str,
        rr_type: str,
        value: str,
        ttl: int | None,
        priority: int | None,
    ) -> Record:
        """Validate and assemble a :class:`Record` from caller inputs."""
        _validate_zone(zone)
        return Record(
            name=_validate_name(name),
            type=_validate_type(rr_type),
            value=_validate_value(value),
            ttl=_validate_ttl(ttl),
            priority=priority,
        )

    def _call(self, backend_call: Any) -> Any:
        """Resolve credentials at call time and route to the backend.

        Backend failures (auth, missing record, conflict, provider error) surface
        as :class:`DnsError` so the MCP layer maps them to a clean ``ToolError``.
        Credentials are resolved per call and never retained on the service.
        """
        credentials = self._credentials.resolve()
        try:
            return backend_call(credentials)
        except BackendError as error:
            raise DnsError(str(error)) from error


# Re-exported so importers get the full model from one module.
__all__ = [
    "MAX_TTL",
    "MIN_TTL",
    "SUPPORTED_TYPES",
    "CredentialProvider",
    "DnsError",
    "DnsService",
    "EnvCredentialProvider",
    "Record",
    "RecordKey",
]
