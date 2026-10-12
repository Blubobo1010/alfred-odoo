"""Credentials and settings. Secrets come from Hermes' profile-scoped secret store only."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .client import PROTOCOLS, OdooClient, OdooError, normalize_areas

REQUIRED_ENV = ("ODOO_URL", "ODOO_API_KEY")
JSONRPC_ENV = ("ODOO_DB", "ODOO_USERNAME")  # also required when the JSON-RPC API is used
ALL_ENV = REQUIRED_ENV[:1] + JSONRPC_ENV + REQUIRED_ENV[1:]

_ctx: Any = None  # the PluginContext handed to register(); settings are read live from it


def bind_context(ctx: Any) -> None:
    global _ctx
    _ctx = ctx


def get_secret(name: str) -> str:
    """Profile-aware secret lookup (``agent.secret_scope``); plain environment outside Hermes (dev/tests)."""
    try:
        from agent.secret_scope import get_secret as _hermes_get_secret
    except ImportError:  # running outside a Hermes runtime (unit tests, `python -m` smoke)
        return os.environ.get(name, "") or ""
    return _hermes_get_secret(name) or ""


def setting(key: str, default: Any) -> Any:
    if _ctx is None:
        return default
    try:
        value = _ctx.get_config(key, default)
    except Exception:  # noqa: BLE001 - a bad config path must not break a tool call
        return default
    return default if value is None else value


@dataclass(frozen=True)
class Settings:
    default_limit: int = 20
    timezone: str = ""
    lang: str = ""
    company_ids: tuple[int, ...] = ()
    default_team: str = ""
    timeout_seconds: int = 20
    max_body_chars: int = 800
    advanced_tools: bool = False
    write_enabled: bool = False
    enabled_areas: frozenset[str] = frozenset()
    unknown_areas: tuple[str, ...] = ()
    revenue_range: tuple[float, float] | None = None
    custom_fields: tuple[tuple[str, str], ...] = ()
    config_errors: tuple[str, ...] = ()
    api: str = "auto"


_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,30}$")
_FIELD = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def parse_revenue_range(value: Any) -> tuple[float, float] | None:
    """``revenue_range`` setting ([min, max] or "min,max") -> bounds; empty = no plausibility check.
    A max of 0 means no upper bound."""
    if isinstance(value, str):
        value = [v for v in value.split(",") if v.strip()]
    if not value:
        return None
    if len(value) != 2:
        raise OdooError("bad_config", "revenue_range must be two numbers: [min, max]")
    low, high = float(value[0]), float(value[1])
    if low < 0 or (high and high <= low):
        raise OdooError("bad_config", "revenue_range needs 0 <= min < max (max 0 = no upper bound)")
    return low, high or float("inf")


def parse_custom_fields(value: Any) -> tuple[tuple[str, str], ...]:
    """``custom_fields`` setting (["label=field", ...] or "label=field,...") -> ((label, field), ...)."""
    if isinstance(value, str):
        value = value.split(",")
    out: dict[str, str] = {}
    for item in value or []:
        label, sep, field = str(item).partition("=")
        label, field = label.strip().lower(), field.strip()
        if not sep or not _LABEL.match(label) or not _FIELD.match(field):
            raise OdooError("bad_config", f"custom_fields entry {str(item)!r} must look like label=field_name")
        out[label] = field
    return tuple(out.items())


def load_settings() -> Settings:
    ids = setting("company_ids", []) or []
    areas, unknown = normalize_areas(setting("enabled_areas", []))
    errors: list[str] = []
    revenue_range = _parsed(parse_revenue_range, setting("revenue_range", []), None, errors)
    custom_fields = _parsed(parse_custom_fields, setting("custom_fields", []), (), errors)
    api = str(setting("api", "auto") or "auto").strip().lower()
    if api not in ("auto", *PROTOCOLS):
        errors.append(f"api must be auto, {' or '.join(PROTOCOLS)} (got {api!r})")
        api = "auto"
    return Settings(
        default_limit=int(setting("default_limit", 20) or 20),
        timezone=str(setting("timezone", "") or ""),
        lang=str(setting("lang", "") or ""),
        company_ids=tuple(int(i) for i in ids if str(i).strip()),
        default_team=str(setting("default_team", "") or ""),
        timeout_seconds=int(setting("timeout_seconds", 20) or 20),
        max_body_chars=int(setting("max_body_chars", 800) or 800),
        advanced_tools=bool(setting("advanced_tools", False)),
        write_enabled=bool(setting("write_enabled", False)),
        enabled_areas=areas,
        unknown_areas=unknown,
        revenue_range=revenue_range,
        custom_fields=custom_fields,
        config_errors=tuple(errors),
        api=api,
    )


def resolve_protocol(settings: Settings, env: dict[str, str]) -> str:
    """``api: auto`` = JSON-RPC when database and login are both set (installs before 0.4.0), else JSON-2."""
    if settings.api != "auto":
        return settings.api
    return "jsonrpc" if all(env.get(n) for n in JSONRPC_ENV) else "json2"


def _parsed(parse: Any, value: Any, fallback: Any, errors: list[str]) -> Any:
    """A malformed optional setting must not hide the tools (check_fn) but must fail the call loudly."""
    try:
        return parse(value)
    except (OdooError, ValueError, TypeError) as exc:
        errors.append(getattr(exc, "message", None) or str(exc))
        return fallback


def is_configured() -> bool:
    """check_fn for every tool: never touches the network (tool availability is cached by Hermes)."""
    try:
        env = {n: get_secret(n) for n in ALL_ENV}
        needed = REQUIRED_ENV + (JSONRPC_ENV if resolve_protocol(load_settings(), env) == "jsonrpc" else ())
        return all(env[n] for n in needed)
    except Exception:  # noqa: BLE001 - UnscopedSecretError & co.: treat as not configured
        return False


def tool_check(*areas: str, any_of: bool = False, advanced: bool = False, write: bool = False):
    """check_fn for a tool: credentials present, its data areas enabled, and the advanced/write opt-ins.
    Never touches the network (Hermes caches tool availability)."""

    def check() -> bool:
        if not is_configured():
            return False
        s = load_settings()
        if advanced and not s.advanced_tools:
            return False
        if write and not s.write_enabled:
            return False
        if not areas:
            return bool(s.enabled_areas)
        wanted = set(areas)
        return bool(wanted & s.enabled_areas) if any_of else wanted <= s.enabled_areas

    check.areas = frozenset(areas)  # type: ignore[attr-defined]  # introspected by tests
    check.write = write  # type: ignore[attr-defined]
    return check


def advanced_enabled() -> bool:
    return is_configured() and load_settings().advanced_tools


def write_enabled() -> bool:
    """Writes opted in by the operator (the write tools' check_fn also requires their areas)."""
    return is_configured() and load_settings().write_enabled


def make_client(settings: Settings | None = None) -> OdooClient:
    settings = settings or load_settings()
    values = {n: get_secret(n) for n in ALL_ENV}
    protocol = resolve_protocol(settings, values)
    needed = REQUIRED_ENV + (JSONRPC_ENV if protocol == "jsonrpc" else ())
    missing = [n for n in needed if not values[n]]
    if missing:
        raise OdooError("not_configured", "Odoo credentials are missing: " + ", ".join(missing),
                        "Set them in the Alfred .env (alfred plugins install prompts for them) and restart the gateway.")
    return OdooClient(values["ODOO_URL"], values["ODOO_DB"], values["ODOO_USERNAME"], values["ODOO_API_KEY"],
                      areas=settings.enabled_areas, timeout=settings.timeout_seconds, protocol=protocol)


def call_context(client: OdooClient, settings: Settings, **extra: Any) -> dict[str, Any]:
    """Context sent with every call: language, time zone and the company scope."""
    user = client.user_context()
    ctx: dict[str, Any] = {"lang": settings.lang or user["lang"], "tz": settings.timezone or user["tz"]}
    companies = list(settings.company_ids) or user["company_ids"]
    if companies:
        ctx["allowed_company_ids"] = companies
    ctx.update(extra)
    return ctx
