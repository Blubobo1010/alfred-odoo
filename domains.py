"""Pure helpers that turn tool arguments into Odoo domains (easy to unit-test without a server)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from .client import OdooClient, OdooError

STATUSES = ("open", "won", "lost", "all")
LEAD_TYPES = ("opportunity", "lead", "any")
SORTS = {
    "deadline": "date_deadline asc, priority desc",
    "revenue_desc": "expected_revenue desc",
    "recent": "create_date desc",
    "probability_desc": "probability desc, expected_revenue desc",
    "name": "name asc",
}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class Resolution(dict):
    """Names the tool resolved loosely, echoed back so the model can disambiguate."""


def status_domain(status: str, *, has_won_status: bool) -> tuple[list[Any], dict[str, Any]]:
    """Domain + context for open / won / lost / all (lost leads are archived in Odoo)."""
    status = (status or "open").lower()
    if status not in STATUSES:
        raise OdooError("bad_argument", f"status must be one of {', '.join(STATUSES)}")
    if status == "all":
        return [], {"active_test": False}
    if has_won_status:  # Odoo 17+: computed won_status = pending | won | lost
        key = {"open": "pending", "won": "won", "lost": "lost"}[status]
        ctx = {"active_test": False} if status == "lost" else {}
        return [("won_status", "=", key)], ctx
    if status == "open":
        return [("active", "=", True), ("stage_id.is_won", "=", False)], {}
    if status == "won":
        return [("stage_id.is_won", "=", True)], {}
    return [("active", "=", False), ("probability", "=", 0)], {"active_test": False}


def parse_date(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text == "today":
        return date.today().isoformat()
    if not _DATE_RE.match(text):
        raise OdooError("bad_argument", f"{name} must be an ISO date (YYYY-MM-DD), got {text!r}")
    return text


def date_range(field: str, start: Any, end: Any, *, is_datetime: bool = False) -> list[Any]:
    domain: list[Any] = []
    s, e = parse_date(start, "from"), parse_date(end, "to")
    if s:
        domain.append((field, ">=", f"{s} 00:00:00" if is_datetime else s))
    if e:
        domain.append((field, "<=", f"{e} 23:59:59" if is_datetime else e))
    return domain


def text_domain(text: str | None, fields: list[str]) -> list[Any]:
    """OR over several ``ilike`` fields: ['|','|',(a),(b),(c)]."""
    text = (text or "").strip()
    if not text or not fields:
        return []
    leaves = [(f, "ilike", text) for f in fields]
    return ["|"] * (len(leaves) - 1) + leaves


def resolve_ids(client: OdooClient, model: str, text: str | None, *, domain: list[Any] | None = None,
                label: str, resolved: Resolution, vocabulary: list[str] | None = None,
                context: dict[str, Any] | None = None) -> list[int] | None:
    """Loose name match via name_search; None when no filter was asked for.

    0 matches -> bad_argument with the known values; >1 matches -> all ids (and the names are echoed).
    """
    text = (text or "").strip()
    if not text:
        return None
    hits = client.name_search(model, text, domain=domain, limit=10, context=context)
    if not hits:
        known = vocabulary or [n for _, n in client.name_search(model, "", domain=domain, limit=50, context=context)]
        raise OdooError("no_match", f"no {label} matches {text!r}", hint="Known values: " + ", ".join(known[:50]))
    exact = [h for h in hits if h[1].strip().lower() == text.lower()]
    chosen = exact if exact else hits
    resolved[label] = [n for _, n in chosen] if len(chosen) > 1 else chosen[0][1]
    return [i for i, _ in chosen]


def resolve_one(client: OdooClient, model: str, text: str | None, *, domain: list[Any] | None = None,
                label: str, resolved: Resolution, context: dict[str, Any] | None = None) -> int:
    """Like ``resolve_ids`` but for a write target: exactly one record, or an ``ambiguous`` error."""
    text = (text or "").strip()
    if not text:
        raise OdooError("bad_argument", f"{label} is required")
    ids = resolve_ids(client, model, text, domain=domain, label=label, resolved=resolved, context=context) or []
    if len(ids) != 1:
        names = resolved.get(label)
        raise OdooError("ambiguous", f"{label} {text!r} matches several records: " + ", ".join(names if isinstance(names, list) else [str(names)]),
                        hint="Repeat the call with one of these exact names.")
    return ids[0]


def user_ids_for(client: OdooClient, who: str | None, *, resolved: Resolution,
                 context: dict[str, Any] | None = None) -> list[int] | None:
    if who and who.strip().lower() in ("me", "myself", "mine", "io"):
        resolved["salesperson"] = client.user_context().get("name") or "me"
        return [client.uid]
    return resolve_ids(client, "res.users", who, domain=[("share", "=", False)], label="salesperson",
                       resolved=resolved, context=context)


def lead_domain(client: OdooClient, args: dict[str, Any], *, default_team: str = "") -> tuple[list[Any], dict[str, Any], Resolution]:
    """Domain, extra context and resolved names for odoo_search_leads / odoo_pipeline_summary."""
    fields = client.fields_get("crm.lead")
    resolved = Resolution()
    domain: list[Any] = []
    domain_status, ctx = status_domain(args.get("status", "open"), has_won_status="won_status" in fields)
    domain += domain_status

    lead_type = (args.get("type") or "opportunity").lower()
    if lead_type not in LEAD_TYPES:
        raise OdooError("bad_argument", f"type must be one of {', '.join(LEAD_TYPES)}")
    if lead_type != "any" and "type" in fields:
        domain.append(("type", "=", lead_type))

    domain += text_domain(args.get("text"), [f for f in ("name", "partner_id.name", "contact_name", "email_from") if f.split(".")[0] in fields])

    if args.get("partner_id"):
        domain.append(("partner_id", "child_of", int(args["partner_id"])))
    stage_ids = resolve_ids(client, "crm.stage", args.get("stage"), label="stage", resolved=resolved)
    if stage_ids:
        domain.append(("stage_id", "in", stage_ids))
    user_ids = user_ids_for(client, args.get("salesperson"), resolved=resolved)
    if user_ids:
        domain.append(("user_id", "in", user_ids))
    team_text = args.get("team") or default_team
    team_ids = resolve_ids(client, "crm.team", team_text, label="team", resolved=resolved)
    if team_ids:
        domain.append(("team_id", "in", team_ids))
    if "tag_ids" in fields:
        tag_ids = resolve_ids(client, "crm.tag", args.get("tag"), label="tag", resolved=resolved)
        if tag_ids:
            domain.append(("tag_ids", "in", tag_ids))

    if args.get("min_revenue") is not None:
        domain.append(("expected_revenue", ">=", float(args["min_revenue"])))
    if args.get("max_revenue") is not None:
        domain.append(("expected_revenue", "<=", float(args["max_revenue"])))
    domain += date_range("date_deadline", args.get("deadline_from"), args.get("deadline_to"))
    domain += date_range("create_date", args.get("created_from"), args.get("created_to"), is_datetime=True)
    return domain, ctx, resolved


def lead_order(sort: str | None) -> str:
    key = (sort or "deadline").lower()
    if key not in SORTS:
        raise OdooError("bad_argument", f"sort must be one of {', '.join(SORTS)}")
    return SORTS[key]


def activity_window(due: str | None, today: date | None = None) -> tuple[list[Any], str]:
    today = today or date.today()
    key = (due or "overdue_and_today").lower()
    iso = today.isoformat()
    windows: dict[str, list[Any]] = {
        "overdue": [("date_deadline", "<", iso)],
        "today": [("date_deadline", "=", iso)],
        "overdue_and_today": [("date_deadline", "<=", iso)],
        "this_week": [("date_deadline", ">=", (today - timedelta(days=today.weekday())).isoformat()),
                      ("date_deadline", "<=", (today + timedelta(days=6 - today.weekday())).isoformat())],
        "next_7_days": [("date_deadline", ">=", iso), ("date_deadline", "<=", (today + timedelta(days=7)).isoformat())],
        "all": [],
    }
    if key not in windows:
        raise OdooError("bad_argument", f"due must be one of {', '.join(windows)}")
    return windows[key], key


def activity_state(deadline: str | None, today: date | None = None) -> str:
    today = today or date.today()
    if not deadline:
        return "planned"
    try:
        d = datetime.strptime(deadline[:10], "%Y-%m-%d").date()
    except ValueError:
        return "planned"
    return "overdue" if d < today else "today" if d == today else "planned"


# ---- odoo_query validation -------------------------------------------------------------------

_FIELD_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*){0,3}$")
_OPERATORS = frozenset({"=", "!=", ">", ">=", "<", "<=", "like", "ilike", "not like", "not ilike", "in", "not in",
                        "child_of", "=like", "=ilike", "is", "is not"})
MAX_DOMAIN_TERMS = 30


def validate_domain(domain: Any) -> list[Any]:
    if domain in (None, ""):
        return []
    if not isinstance(domain, list):
        raise OdooError("bad_argument", "domain must be a list (Odoo polish-notation domain)")
    if len(domain) > MAX_DOMAIN_TERMS:
        raise OdooError("bad_argument", f"domain has more than {MAX_DOMAIN_TERMS} terms")
    out: list[Any] = []
    for term in domain:
        if term in ("&", "|", "!"):
            out.append(term)
            continue
        if not isinstance(term, list | tuple) or len(term) != 3:
            raise OdooError("bad_argument", f"invalid domain term {term!r}: expected [field, operator, value]")
        fld, op, val = term
        if not isinstance(fld, str) or not _FIELD_RE.match(fld):
            raise OdooError("bad_argument", f"invalid field name {fld!r}")
        if not isinstance(op, str) or op.lower() not in _OPERATORS:
            raise OdooError("bad_argument", f"operator {op!r} not allowed")
        if isinstance(val, list | tuple):
            if not all(isinstance(v, str | int | float | bool) or v is None for v in val):
                raise OdooError("bad_argument", "list values must be scalars")
        elif not (isinstance(val, str | int | float | bool) or val is None):
            raise OdooError("bad_argument", "values must be scalars or lists of scalars")
        out.append([fld, op.lower(), list(val) if isinstance(val, tuple) else val])
    return out
