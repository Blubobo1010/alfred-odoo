"""alfred-odoo — Odoo CRM tools for Hermes / Alfred: read tools plus three opt-in, human-approved writes.

Everything here registers synchronously and without I/O: Hermes loads plugins at startup and
``hermes plugins doctor`` runs ``register()`` with the network blocked. Availability is decided
per call by ``check_fn`` (credentials present, the tool's data areas enabled, writes enabled) —
never by skipping a registration. Data areas are default-deny (``enabled_areas``, see client.py).
"""

from __future__ import annotations

from pathlib import Path

from . import schemas, tools
from .approvals import pre_tool_call
from .cli import odoo_command, register_cli
from .settings import bind_context, tool_check

TOOLSET = "odoo"

_TOOLS = (
    ("odoo_crm_vocabulary", schemas.ODOO_CRM_VOCABULARY, tools.odoo_crm_vocabulary, "📇", tool_check("crm")),
    ("odoo_search_leads", schemas.ODOO_SEARCH_LEADS, tools.odoo_search_leads, "📇", tool_check("crm")),
    ("odoo_get_lead", schemas.ODOO_GET_LEAD, tools.odoo_get_lead, "📇", tool_check("crm")),
    ("odoo_pipeline_summary", schemas.ODOO_PIPELINE_SUMMARY, tools.odoo_pipeline_summary, "📊", tool_check("crm")),
    ("odoo_search_partners", schemas.ODOO_SEARCH_PARTNERS, tools.odoo_search_partners, "🏢", tool_check("contacts")),
    ("odoo_timeline", schemas.ODOO_TIMELINE, tools.odoo_timeline, "🕒", tool_check("chatter", "activities", any_of=True)),
    ("odoo_list_activities", schemas.ODOO_LIST_ACTIVITIES, tools.odoo_list_activities, "✅", tool_check("activities")),
    ("odoo_describe_model", schemas.ODOO_DESCRIBE_MODEL, tools.odoo_describe_model, "🧭", tool_check(advanced=True)),
    ("odoo_query", schemas.ODOO_QUERY, tools.odoo_query, "🧭", tool_check(advanced=True)),
    ("odoo_log_note", schemas.ODOO_LOG_NOTE, tools.odoo_log_note, "✍️", tool_check("crm", write=True)),
    ("odoo_schedule_activity", schemas.ODOO_SCHEDULE_ACTIVITY, tools.odoo_schedule_activity, "✍️",
     tool_check("crm", "activities", write=True)),
    ("odoo_update_lead_stage", schemas.ODOO_UPDATE_LEAD_STAGE, tools.odoo_update_lead_stage, "✍️", tool_check("crm", write=True)),
)
REQUIRED_ENV = ["ODOO_URL", "ODOO_API_KEY"]  # + ODOO_DB / ODOO_USERNAME for JSON-RPC (settings.make_client)


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
