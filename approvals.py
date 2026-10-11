"""Human approval for the write tools, through Hermes' ``pre_tool_call`` hook.

Hermes calls ``pre_tool_call`` before every tool. For our write tools it answers
``{"action": "approve", "message": <what will change>, "rule_key": <per-call key>}``: Hermes then
asks the human through the same gate as dangerous shell commands (CLI prompt or Telegram card,
once / session / always / deny, fail-closed on timeout). The prompt shows only the tool name and
``message``, so the message must describe the change on its own. ``rule_key`` is a hash of the
tool and its arguments: "always" pre-approves only the identical change, never the whole tool.

The hook must be fast (Hermes gives it 30 s and blocks on timeout) and must never let a write
through by accident: no network here, and any internal failure returns ``block``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .settings import load_settings

WRITE_TOOLS = ("odoo_log_note", "odoo_schedule_activity", "odoo_update_lead_stage")
SUMMARY_MAX_CHARS = 280
_ELLIPSIS = "…"


def _short(text: Any, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + _ELLIPSIS


def approval_summary(tool_name: str, args: dict[str, Any] | None) -> str:
    """One line, readable by a non-technical approver, built from the raw arguments (no lookups)."""
    args = args or {}
    lead = f"lead #{args.get('lead_id', '?')}"
    if tool_name == "odoo_log_note":
        text = f'Odoo: log an internal note on {lead}: "{_short(args.get("note"), 160)}"'
    elif tool_name == "odoo_schedule_activity":
        extras = [f"type {args['activity_type']}" if args.get("activity_type") else "",
                  f"due {args.get('deadline') or 'today'}",
                  f"assigned to {args.get('assign_to') or 'the connected user'}"]
        text = f'Odoo: schedule activity "{_short(args.get("summary"), 80)}" on {lead} ({", ".join(e for e in extras if e)})'
    elif tool_name == "odoo_update_lead_stage":
        text = f'Odoo: move {lead} to stage "{_short(args.get("stage"), 60)}"'
    else:
        text = f"Odoo: {tool_name} {_short(json.dumps(args, ensure_ascii=False, sort_keys=True, default=str), 200)}"
    return _short(text, SUMMARY_MAX_CHARS)


def rule_key(tool_name: str, args: dict[str, Any] | None) -> str:
    canonical = json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"alfred-odoo:{tool_name}:{digest}"


def pre_tool_call(tool_name: str = "", args: Any = None, **_: Any) -> dict[str, str] | None:
    """Hermes ``pre_tool_call`` hook: None for everything that is not one of our write tools."""
    if tool_name not in WRITE_TOOLS:
        return None
    try:
        call_args = dict(args) if isinstance(args, dict) else {}
        if not load_settings().write_enabled:
            return {"action": "block", "message": "BLOCKED: Odoo writes are disabled "
                    "(set write_enabled: true in the alfred-odoo plugin settings)."}
        return {"action": "approve", "message": approval_summary(tool_name, call_args),
                "rule_key": rule_key(tool_name, call_args)}
    except Exception as exc:  # noqa: BLE001 - never let a write slip through on a hook failure
        return {"action": "block", "message": f"BLOCKED: alfred-odoo approval hook failed ({exc.__class__.__name__})."}
