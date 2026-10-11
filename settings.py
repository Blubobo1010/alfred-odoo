"""Credentials and settings. Secrets come from Hermes' profile-scoped secret store only."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from .client import OdooClient, OdooError

REQUIRED_ENV = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")

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


def load_settings() -> Settings:
    ids = setting("company_ids", []) or []
    return Settings(
        default_limit=int(setting("default_limit", 20) or 20),
        timezone=str(setting("timezone", "") or ""),
        lang=str(setting("lang", "") or ""),
        company_ids=tuple(int(i) for i in ids if str(i).strip()),
        default_team=str(setting("default_team", "") or ""),
        timeout_seconds=int(setting("timeout_seconds", 20) or 20),
        max_body_chars=int(setting("max_body_chars", 800) or 800),
        advanced_tools=bool(setting("advanced_tools", False)),
    )


def is_configured() -> bool:
    """check_fn for every tool: never touches the network (tool availability is cached by Hermes)."""
    try:
        return all(get_secret(n) for n in REQUIRED_ENV)
    except Exception:  # noqa: BLE001 - UnscopedSecretError & co.: treat as not configured
        return False


def advanced_enabled() -> bool:
    return is_configured() and load_settings().advanced_tools


def make_client(settings: Settings | None = None) -> OdooClient:
    settings = settings or load_settings()
    values = {n: get_secret(n) for n in REQUIRED_ENV}
    missing = [n for n, v in values.items() if not v]
    if missing:
        raise OdooError("not_configured", "Odoo credentials are missing: " + ", ".join(missing),
                        "Set them in the Hermes .env (hermes plugins install prompts for them) and restart the gateway.")
    return OdooClient(values["ODOO_URL"], values["ODOO_DB"], values["ODOO_USERNAME"], values["ODOO_API_KEY"],
                      timeout=settings.timeout_seconds)


def call_context(client: OdooClient, settings: Settings, **extra: Any) -> dict[str, Any]:
    """Context sent with every call: language, time zone and the company scope."""
    user = client.user_context()
    ctx: dict[str, Any] = {"lang": settings.lang or user["lang"], "tz": settings.timezone or user["tz"]}
    companies = list(settings.company_ids) or user["company_ids"]
    if companies:
        ctx["allowed_company_ids"] = companies
    ctx.update(extra)
    return ctx
