"""Minimal Odoo JSON-RPC client (stdlib only).

Reads go through ``execute_kw`` with a method allow-list and a model allow-list, so a misused
read tool still cannot write to Odoo. On top of that every model belongs to a data *area*
(``AREA_MODELS``) and records of an area are only read or written once the operator has enabled
that area (``enabled_areas``): default-deny, because a customer's Odoo data is often not clean
enough to answer from until someone has checked it. The only writes are the explicit ``write_kw`` operations in
``WRITE_OPS`` (a chatter note, a scheduled activity, a stage move on ``crm.lead``), and the
``crm.lead`` write is restricted to ``LEAD_WRITABLE_FIELDS``. Sessions (uid, server version, user
context, ``fields_get``) are cached per credential set and guarded by a lock: the gateway calls
tool handlers from several threads and one process may serve several profiles.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

__version__ = "0.3.0"

READ_METHODS = frozenset({"search_read", "read", "search", "search_count", "read_group", "fields_get", "name_search"})

# Data areas: records of these models are only touched when the area is in ``enabled_areas``.
AREA_MODELS: dict[str, frozenset[str]] = {
    "crm": frozenset({"crm.lead", "crm.stage", "crm.team", "crm.tag", "crm.lost.reason"}),
    "contacts": frozenset({"res.partner"}),
    "activities": frozenset({"mail.activity", "mail.activity.type"}),
    "chatter": frozenset({"mail.message", "mail.tracking.value"}),
}
AREAS = tuple(AREA_MODELS)
# Session metadata (connected user, companies, currency, model ids): always readable.
META_MODELS = frozenset({"res.users", "res.company", "res.currency", "ir.model"})
ALLOWED_MODELS = META_MODELS.union(*AREA_MODELS.values())
# Schema introspection returns no records, so it works on every allow-listed model: the operator
# checks an area's fields before enabling it.
SCHEMA_METHODS = frozenset({"fields_get"})

# The complete set of write operations this plugin can ever perform (model, method).
WRITE_OPS = frozenset({("crm.lead", "message_post"), ("crm.lead", "write"), ("mail.activity", "create")})
# Fields a ``crm.lead.write`` may touch.
LEAD_WRITABLE_FIELDS = frozenset({"stage_id"})

FIELDS_TTL_SECONDS = 600
Transport = Callable[[str, dict[str, Any], float], dict[str, Any]]


class OdooError(Exception):
    """A failure the tool reports to the model as ``{"ok": false, "error": {...}}``."""

    def __init__(self, code: str, message: str, hint: str = "", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.retryable = retryable

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            out["hint"] = self.hint
        return out


def area_of(model: str) -> str | None:
    """The data area a model belongs to; None for the always-readable metadata models."""
    for area, models in AREA_MODELS.items():
        if model in models:
            return area
    return None


def normalize_areas(values: Any) -> tuple[frozenset[str], tuple[str, ...]]:
    """``enabled_areas`` setting -> (known areas, unknown names). A string is split on commas."""
    if isinstance(values, str):
        values = values.split(",")
    names = [str(v).strip().lower() for v in (values or []) if str(v).strip()]
    known = frozenset(n for n in names if n in AREA_MODELS)
    unknown = tuple(sorted({n for n in names if n not in AREA_MODELS}))
    return known, unknown


class AreaNotEnabled(OdooError):
    def __init__(self, area: str, model: str):
        super().__init__(
            "area_not_enabled", f"the Odoo area {area!r} ({model}) is not enabled for this assistant",
            "Do not retry and do not guess: tell the user this Odoo data is not available yet. The operator "
            "enables an area (enabled_areas in the plugin settings) after checking its data with the customer.")
        self.area = area


class ReadOnlyViolation(OdooError):
    def __init__(self, what: str):
        super().__init__("read_only", f"refused: {what} is not a read operation",
                         "Reads go through execute_kw; the only writes are the allow-listed write_kw operations.")


def default_transport(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": f"alfred-odoo/{__version__}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https/loopback enforced by caller
        body = resp.read()
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OdooError("bad_response", f"Odoo returned non-JSON content ({exc.__class__.__name__})",
                        "Check that ODOO_URL is the base URL of an Odoo instance.") from exc


def normalize_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    for suffix in ("/web", "/odoo", "/jsonrpc", "/xmlrpc"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)]
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise OdooError("bad_url", f"ODOO_URL {url!r} is not an http(s) URL", "Example: https://mycompany.odoo.com")
    if parts.scheme == "http" and not _is_private_host(parts.hostname or ""):
        raise OdooError("bad_url", "plain http:// is only allowed for localhost or private networks",
                        "Use https:// for a public Odoo host.")
    return url


def _is_private_host(host: str) -> bool:
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private
    except ValueError:
        return False


@dataclass
class _Session:
    uid: int | None = None
    version: dict[str, Any] | None = None
    user_context: dict[str, Any] | None = None
    fields: dict[str, tuple[float, dict[str, Any]]] = field(default_factory=dict)


_sessions: dict[str, _Session] = {}
_sessions_lock = threading.Lock()


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret and secret in text else text


def map_error(payload: dict[str, Any], secret: str = "") -> OdooError:
    """Translate an Odoo JSON-RPC error payload into a stable error code."""
    err = payload.get("error") or {}
    data = err.get("data") or {}
    name = str(data.get("name") or "")
    message = _scrub(str(data.get("message") or err.get("message") or "Odoo error"), secret)
    debug = _scrub(str(data.get("debug") or ""), secret)
    low = (message + " " + name + " " + debug).lower()
    if "accessdenied" in name.lower() or "access denied" in low:
        return OdooError("auth_failed", message, "Check ODOO_USERNAME and ODOO_API_KEY (Odoo 18 keys can expire); "
                         "run `hermes odoo test`.")
    if "accesserror" in name.lower():
        return OdooError("access_denied", message, "The Odoo user lacks access to this model or operation "
                         "(access rights, record rules or multi-company).")
    if "validationerror" in name.lower() or "usererror" in name.lower():
        return OdooError("validation_error", message[:300], "Odoo refused the change; check the values "
                         "(stage, deadline, assignee) and the record's state.")
    if "doesn't exist" in low and "object" in low or "model not found" in low:
        return OdooError("model_not_installed", message, "Is the CRM app installed on this Odoo?")
    if "invalid field" in low or "unknown field" in low:
        return OdooError("invalid_field", message, "Call odoo_describe_model(model) to list valid fields "
                         "(or `hermes odoo fields <model>`).")
    if "database" in low and ("does not exist" in low or "not exist" in low):
        return OdooError("bad_database", message, "Check ODOO_DB.")
    return OdooError("odoo_error", message[:300])


class OdooClient:
    def __init__(self, url: str, db: str, username: str, api_key: str, *, areas: Any = (),
                 timeout: float = 20.0, transport: Transport | None = None):
        self.url = normalize_url(url)
        self.db = db.strip()
        self.username = username.strip()
        self._api_key = api_key
        self.timeout = float(timeout)
        self._transport = transport or default_transport
        self.areas, _ = normalize_areas(areas)
        self._key = hashlib.sha256(f"{self.url}|{self.db}|{self.username}|{api_key}".encode()).hexdigest()
        if not (self.db and self.username and api_key):
            raise OdooError("not_configured", "ODOO_DB, ODOO_USERNAME and ODOO_API_KEY must all be set")

    # ---- plumbing ---------------------------------------------------------------------------

    def _session(self) -> _Session:
        with _sessions_lock:
            return _sessions.setdefault(self._key, _Session())

    def _rpc(self, service: str, method: str, args: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "method": "call", "id": int(time.time() * 1000) % 1_000_000,
                   "params": {"service": service, "method": method, "args": args}}
        try:
            response = self._transport(f"{self.url}/jsonrpc", payload, self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise OdooError("bad_url", f"{self.url}/jsonrpc not found (HTTP 404)",
                                "ODOO_URL must be the base URL of the Odoo instance.") from exc
            if exc.code in (429, 502, 503, 504):
                raise OdooError("rate_limited", f"Odoo answered HTTP {exc.code}", retryable=True) from exc
            raise OdooError("odoo_error", f"HTTP {exc.code} from Odoo") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            code = "timeout" if "timed out" in str(reason).lower() else "unreachable"
            raise OdooError(code, f"cannot reach {self.url}: {reason}", "Check ODOO_URL and the network.",
                            retryable=True) from exc
        if not isinstance(response, dict):
            raise OdooError("bad_response", "unexpected JSON-RPC payload")
        if response.get("error"):
            raise map_error(response, self._api_key)
        return response.get("result")

    # ---- session ----------------------------------------------------------------------------

    def version(self) -> dict[str, Any]:
        s = self._session()
        if s.version is None:
            s.version = self._rpc("common", "version", []) or {}
        return s.version

    @property
    def server_major(self) -> int:
        info = self.version().get("server_version_info") or [0]
        try:
            return int(info[0])
        except (TypeError, ValueError, IndexError):
            return 0

    @property
    def uid(self) -> int:
        s = self._session()
        if s.uid is None:
            uid = self._rpc("common", "authenticate", [self.db, self.username, self._api_key, {}])
            if not uid:
                raise OdooError("auth_failed", "Odoo rejected the credentials",
                                "Check ODOO_DB, ODOO_USERNAME and ODOO_API_KEY (Odoo 18 keys can expire).")
            s.uid = int(uid)
        return s.uid

    def forget_session(self) -> None:
        with _sessions_lock:
            _sessions.pop(self._key, None)

    def user_context(self) -> dict[str, Any]:
        """tz / lang / company ids of the API user (cached)."""
        s = self._session()
        if s.user_context is None:
            rows = self.read("res.users", [self.uid], ["tz", "lang", "company_id", "company_ids", "name"])
            row = rows[0] if rows else {}
            s.user_context = {
                "name": row.get("name"),
                "tz": row.get("tz") or "UTC",
                "lang": row.get("lang") or "en_US",
                "company_id": (row.get("company_id") or [None])[0],
                "company_ids": list(row.get("company_ids") or []),
            }
        return s.user_context

    # ---- area gate --------------------------------------------------------------------------

    def area_enabled(self, area: str) -> bool:
        return area in self.areas

    def require_area(self, model: str) -> None:
        area = area_of(model)
        if area is not None and area not in self.areas:
            raise AreaNotEnabled(area, model)

    # ---- read-only execute_kw ----------------------------------------------------------------

    def execute_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        if method not in READ_METHODS:
            raise ReadOnlyViolation(f"{model}.{method}")
        if model not in ALLOWED_MODELS:
            raise OdooError("model_not_allowed", f"model {model!r} is not in the allow-list",
                            "Allowed: " + ", ".join(sorted(ALLOWED_MODELS)))
        if method not in SCHEMA_METHODS:
            self.require_area(model)
        try:
            return self._rpc("object", "execute_kw", [self.db, self.uid, self._api_key, model, method, args, kwargs or {}])
        except OdooError as exc:
            if exc.code == "auth_failed":
                self.forget_session()  # retry once with a fresh uid (expired session / key rotation)
                return self._rpc("object", "execute_kw",
                                 [self.db, self.uid, self._api_key, model, method, args, kwargs or {}])
            raise

    # ---- allow-listed writes -----------------------------------------------------------------

    def write_kw(self, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None) -> Any:
        """The only path that can change Odoo: ``(model, method)`` must be in ``WRITE_OPS`` and a
        ``crm.lead.write`` may only touch ``LEAD_WRITABLE_FIELDS``. Callers are the write tools,
        which Hermes gates behind the human approval hook (see ``approvals.py``)."""
        if (model, method) not in WRITE_OPS:
            raise OdooError("write_not_allowed", f"refused: {model}.{method} is not an allow-listed write",
                            "Allowed: " + ", ".join(sorted(f"{m}.{op}" for m, op in WRITE_OPS)))
        self.require_area(model)
        if method == "write":
            values = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
            bad = sorted(set(values) - LEAD_WRITABLE_FIELDS)
            if bad or not values:
                raise OdooError("field_not_writable", "refused: cannot write " + (", ".join(bad) or "nothing"),
                                "Writable: " + ", ".join(sorted(LEAD_WRITABLE_FIELDS)))
        payload = [self.db, self.uid, self._api_key, model, method, args, kwargs or {}]
        try:
            return self._rpc("object", "execute_kw", payload)
        except OdooError as exc:
            if exc.code == "auth_failed":  # nothing was executed: safe to retry once with a fresh uid
                self.forget_session()
                payload[1] = self.uid
                return self._rpc("object", "execute_kw", payload)
            raise

    def search_read(self, model: str, domain: list[Any], fields: list[str], *, limit: int | None = None,
                    offset: int = 0, order: str | None = None, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields, "offset": offset}
        if limit is not None:
            kwargs["limit"] = limit
        if order:
            kwargs["order"] = order
        if context:
            kwargs["context"] = context
        return self.execute_kw(model, "search_read", [domain], kwargs) or []

    def search_count(self, model: str, domain: list[Any], *, context: dict[str, Any] | None = None) -> int:
        kwargs = {"context": context} if context else {}
        return int(self.execute_kw(model, "search_count", [domain], kwargs) or 0)

    def read(self, model: str, ids: list[int], fields: list[str], *, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields}
        if context:
            kwargs["context"] = context
        return self.execute_kw(model, "read", [ids], kwargs) or []

    def read_group(self, model: str, domain: list[Any], fields: list[str], groupby: list[str], *,
                   context: dict[str, Any] | None = None, orderby: str | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"fields": fields, "groupby": groupby, "lazy": False}
        if orderby:
            kwargs["orderby"] = orderby
        if context:
            kwargs["context"] = context
        return self.execute_kw(model, "read_group", [domain], kwargs) or []

    def name_search(self, model: str, name: str, *, domain: list[Any] | None = None, limit: int = 10,
                    context: dict[str, Any] | None = None) -> list[tuple[int, str]]:
        kwargs: dict[str, Any] = {"args": domain or [], "limit": limit}
        if context:
            kwargs["context"] = context
        rows = self.execute_kw(model, "name_search", [name], kwargs) or []
        return [(int(r[0]), str(r[1])) for r in rows]

    def fields_get(self, model: str) -> dict[str, Any]:
        s = self._session()
        cached = s.fields.get(model)
        if cached and time.time() - cached[0] < FIELDS_TTL_SECONDS:
            return cached[1]
        fields = self.execute_kw(model, "fields_get", [], {"attributes": ["string", "type", "relation", "selection", "store"]}) or {}
        s.fields[model] = (time.time(), fields)
        return fields

    def available_fields(self, model: str, wanted: list[str]) -> list[str]:
        """``wanted`` filtered to the fields this Odoo version/edition actually has."""
        have = self.fields_get(model)
        return [f for f in wanted if f in have]
