"""alfred-odoo — Odoo CRM tools for Hermes / Alfred: read tools plus three opt-in, human-approved writes.

Everything here registers synchronously and without I/O: Hermes loads plugins at startup and
``hermes plugins doctor`` runs ``register()`` with the network blocked. Availability is decided
per call by ``check_fn`` (credentials present, writes enabled) — never by skipping a registration.
"""

from __future__ import annotations

from pathlib import Path

from . import schemas, tools
from .approvals import pre_tool_call
from .cli import odoo_command, register_cli
from .settings import advanced_enabled, bind_context, is_configured, write_enabled

TOOLSET = "odoo"

_TOOLS = (
    ("odoo_crm_vocabulary", schemas.ODOO_CRM_VOCABULARY, tools.odoo_crm_vocabulary, "📇", is_configured),
    ("odoo_search_leads", schemas.ODOO_SEARCH_LEADS, tools.odoo_search_leads, "📇", is_configured),
    ("odoo_get_lead", schemas.ODOO_GET_LEAD, tools.odoo_get_lead, "📇", is_configured),
    ("odoo_pipeline_summary", schemas.ODOO_PIPELINE_SUMMARY, tools.odoo_pipeline_summary, "📊", is_configured),
    ("odoo_search_partners", schemas.ODOO_SEARCH_PARTNERS, tools.odoo_search_partners, "🏢", is_configured),
    ("odoo_timeline", schemas.ODOO_TIMELINE, tools.odoo_timeline, "🕒", is_configured),
    ("odoo_list_activities", schemas.ODOO_LIST_ACTIVITIES, tools.odoo_list_activities, "✅", is_configured),
    ("odoo_describe_model", schemas.ODOO_DESCRIBE_MODEL, tools.odoo_describe_model, "🧭", advanced_enabled),
    ("odoo_query", schemas.ODOO_QUERY, tools.odoo_query, "🧭", advanced_enabled),
    ("odoo_log_note", schemas.ODOO_LOG_NOTE, tools.odoo_log_note, "✍️", write_enabled),
    ("odoo_schedule_activity", schemas.ODOO_SCHEDULE_ACTIVITY, tools.odoo_schedule_activity, "✍️", write_enabled),
    ("odoo_update_lead_stage", schemas.ODOO_UPDATE_LEAD_STAGE, tools.odoo_update_lead_stage, "✍️", write_enabled),
)
REQUIRED_ENV = ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"]


def register(ctx) -> None:
    bind_context(ctx)
    for name, schema, handler, emoji, check in _TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, check_fn=check,
                          requires_env=REQUIRED_ENV, emoji=emoji)
    # Every write tool call goes through the human approval gate (see approvals.py).
    ctx.register_hook("pre_tool_call", pre_tool_call)
    ctx.register_skill("odoo-crm", Path(__file__).parent / "skills" / "odoo-crm" / "SKILL.md")
    ctx.register_cli_command(
        name="odoo", help="Odoo CRM connection: test, fields, whoami",
        setup_fn=register_cli, handler_fn=odoo_command,
        description="Check the Odoo connection used by the odoo_* tools.",
    )
