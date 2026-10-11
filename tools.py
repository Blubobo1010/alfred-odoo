"""Tool handlers: ``handler(args, **kwargs) -> str`` (JSON). They never raise; errors come back as
``{"ok": false, "error": {...}}`` so the model can recover (wrong name -> known values, etc.).

The three write tools at the end (note, activity, stage) are hidden unless ``write_enabled`` is
set, re-check it on every call, and only reach Odoo through ``OdooClient.write_kw``; Hermes asks
the human before running them (``approvals.py``)."""

from __future__ import annotations

import functools
import html
import json
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from . import domains
from .client import ALLOWED_MODELS, OdooClient, OdooError
from .humanize import (
    UNTRUSTED_NOTICE,
    clamp_limit,
    clamp_offset,
    dumps,
    envelope,
    error,
    fmt_dt,
    html_to_text,
    m2o,
    m2o_id,
    resolve_tz,
)
from .settings import Settings, call_context, load_settings, make_client

LEAD_LIST_FIELDS = [
    "id", "name", "partner_id", "contact_name", "email_from", "phone", "stage_id", "user_id", "team_id",
    "expected_revenue", "prorated_revenue", "probability", "date_deadline", "priority", "tag_ids", "create_date",
    "type", "active", "won_status", "company_currency", "company_id", "activity_date_deadline", "activity_summary",
]
LEAD_DETAIL_EXTRA = [
    "description", "lost_reason_id", "lost_reason", "date_closed", "date_last_stage_update", "date_open",
    "source_id", "medium_id", "campaign_id", "referred", "street", "city", "zip", "country_id", "website",
    "mobile", "function", "activity_type_id", "activity_user_id", "recurring_revenue", "recurring_plan",
]
PARTNER_FIELDS = [
    "id", "name", "display_name", "is_company", "parent_id", "email", "phone", "mobile", "city", "country_id",
    "vat", "user_id", "opportunity_count", "customer_rank", "supplier_rank", "function", "company_id",
]
ACTIVITY_FIELDS = ["id", "summary", "activity_type_id", "date_deadline", "user_id", "res_model", "res_id",
                   "res_name", "note", "create_date"]
MESSAGE_FIELDS = ["id", "date", "author_id", "message_type", "subtype_id", "subject", "body", "model", "res_id",
                  "record_name", "email_from"]
_VOCAB_TTL = 600
_vocab_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _guard(fn: Callable[..., Any]) -> Callable[..., str]:
    """Run a handler against a client + settings; translate every failure into error JSON."""

    @functools.wraps(fn)
    def wrapper(args: dict[str, Any] | None = None, **kwargs: Any) -> str:
        args = dict(args or {})
        try:
            settings = load_settings()
            client = make_client(settings)
            payload = fn(client, settings, args)
            return dumps(payload) if isinstance(payload, dict) else str(payload)
        except OdooError as exc:
            return error(exc.code, exc.message, exc.hint, exc.retryable)
        except (ValueError, TypeError) as exc:
            return error("bad_argument", str(exc)[:300])
        except Exception as exc:  # noqa: BLE001 - the dispatcher must always get JSON back
            return error("internal_error", f"{exc.__class__.__name__}: {str(exc)[:300]}")

    return wrapper


# ---- shared formatting ----------------------------------------------------------------------


def _money(row: dict[str, Any], field: str, currency: str | None) -> dict[str, Any] | None:
    value = row.get(field)
    if value in (None, False):
        return None
    return {"amount": float(value), "currency": currency}


def _lead_status(row: dict[str, Any]) -> str:
    if "won_status" in row and row["won_status"]:
        return {"pending": "open", "won": "won", "lost": "lost"}.get(row["won_status"], str(row["won_status"]))
    if row.get("active") is False:
        return "lost"
    return "open"


def _format_lead(row: dict[str, Any], tz: Any, tag_names: dict[int, str], currency: str | None,
                 max_chars: int, detail: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.get("id"),
        "name": row.get("name"),
        "type": row.get("type"),
        "status": _lead_status(row),
        "partner": {"id": m2o_id(row.get("partner_id")), "name": m2o(row.get("partner_id"))} if row.get("partner_id") else None,
        "contact_name": row.get("contact_name") or None,
        "email": row.get("email_from") or None,
        "phone": row.get("phone") or None,
        "stage": m2o(row.get("stage_id")),
        "salesperson": m2o(row.get("user_id")),
        "team": m2o(row.get("team_id")),
        "expected_revenue": _money(row, "expected_revenue", currency),
        "weighted_revenue": _money(row, "prorated_revenue", currency),
        "probability": row.get("probability"),
        "date_deadline": row.get("date_deadline") or None,
        "priority": {"0": "normal", "1": "low", "2": "high", "3": "very high"}.get(str(row.get("priority")), row.get("priority")),
        "tags": [tag_names.get(i, str(i)) for i in (row.get("tag_ids") or [])] or None,
        "created": fmt_dt(row.get("create_date"), tz),
        "next_activity": (
            {"summary": row.get("activity_summary") or None, "due": row.get("activity_date_deadline") or None}
            if row.get("activity_date_deadline") else None
        ),
    }
    if detail:
        out.update({
            "description": html_to_text(row.get("description"), max_chars) or None,
            "lost_reason": m2o(row.get("lost_reason_id")) or m2o(row.get("lost_reason")),
            "date_closed": fmt_dt(row.get("date_closed"), tz),
            "date_last_stage_update": fmt_dt(row.get("date_last_stage_update"), tz),
            "source": m2o(row.get("source_id")),
            "medium": m2o(row.get("medium_id")),
            "campaign": m2o(row.get("campaign_id")),
            "company": m2o(row.get("company_id")),
            "address": ", ".join(str(row[k]) for k in ("street", "zip", "city") if row.get(k)) or None,
            "country": m2o(row.get("country_id")),
            "website": row.get("website") or None,
            "mobile": row.get("mobile") or None,
            "contact_function": row.get("function") or None,
        })
    return {k: v for k, v in out.items() if v is not None}


def _tag_names(client: OdooClient, rows: list[dict[str, Any]], ctx: dict[str, Any]) -> dict[int, str]:
    ids = sorted({i for r in rows for i in (r.get("tag_ids") or [])})
    if not ids or "crm.tag" not in ALLOWED_MODELS:
        return {}
    return {t["id"]: t["name"] for t in client.read("crm.tag", ids, ["name"], context=ctx)}


def _currency(client: OdooClient, rows: list[dict[str, Any]]) -> str | None:
    for r in rows:
        name = m2o(r.get("company_currency"))
        if name:
            return name
    user = client.user_context()
    if user.get("company_id"):
        comp = client.read("res.company", [user["company_id"]], ["currency_id"])
        return m2o(comp[0].get("currency_id")) if comp else None
    return None


# ---- tools ----------------------------------------------------------------------------------


@_guard
def odoo_crm_vocabulary(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    include = set(args.get("include") or ["stages", "teams", "salespeople", "tags", "lost_reasons", "activity_types"])
    key = f"{client.url}|{client.db}|{client.username}"
    cached = _vocab_cache.get(key)
    if cached and time.time() - cached[0] < _VOCAB_TTL:
        full = cached[1]
    else:
        ctx = call_context(client, settings)
        full: dict[str, Any] = {
            "ok": True,
            "odoo_version": client.version().get("server_version"),
            "connected_user": client.user_context().get("name"),
            "currency": _currency(client, []),
        }
        stage_fields = client.available_fields("crm.stage", ["name", "sequence", "is_won", "fold", "team_id"])
        full["stages"] = [
            {"name": s["name"], "is_won": bool(s.get("is_won")), "folded": bool(s.get("fold")), "team": m2o(s.get("team_id"))}
            for s in client.search_read("crm.stage", [], stage_fields, order="sequence asc, id asc", context=ctx)
        ]
        full["teams"] = [t["name"] for t in client.search_read("crm.team", [], ["name"], order="name asc", context=ctx)]
        full["salespeople"] = [u["name"] for u in client.search_read(
            "res.users", [("share", "=", False), ("active", "=", True)], ["name"], order="name asc", limit=200, context=ctx)]
        full["tags"] = [t["name"] for t in client.search_read("crm.tag", [], ["name"], order="name asc", limit=200, context=ctx)]
        full["lost_reasons"] = [r["name"] for r in client.search_read("crm.lost.reason", [], ["name"], order="name asc", context=ctx)]
        full["activity_types"] = [a["name"] for a in client.search_read(
            "mail.activity.type", ["|", ("res_model", "=", False), ("res_model", "=", "crm.lead")], ["name"], order="sequence asc, id asc", context=ctx)]
        _vocab_cache[key] = (time.time(), full)
    keep = {"ok", "odoo_version", "connected_user", "currency"} | include
    return {k: v for k, v in full.items() if k in keep}


@_guard
def odoo_search_leads(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    limit = clamp_limit(args.get("limit"), settings.default_limit)
    offset = clamp_offset(args.get("offset"))
    domain, extra_ctx, resolved = domains.lead_domain(client, args, default_team=settings.default_team)
    ctx = call_context(client, settings, **extra_ctx)
    fields = client.available_fields("crm.lead", LEAD_LIST_FIELDS)
    rows = client.search_read("crm.lead", domain, fields, limit=limit, offset=offset,
                              order=domains.lead_order(args.get("sort")), context=ctx)
    total = client.search_count("crm.lead", domain, context=ctx)
    tz = resolve_tz(ctx.get("tz"))
    currency = _currency(client, rows)
    tags = _tag_names(client, rows, ctx)
    items = [_format_lead(r, tz, tags, currency, settings.max_body_chars) for r in rows]
    extra = {"resolved": dict(resolved)} if resolved else None
    return envelope(items, total=total, offset=offset, limit=limit, extra=extra)


@_guard
def odoo_get_lead(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    lead_id = int(args["lead_id"])
    ctx = call_context(client, settings, active_test=False)
    fields = client.available_fields("crm.lead", LEAD_LIST_FIELDS + LEAD_DETAIL_EXTRA)
    rows = client.read("crm.lead", [lead_id], fields, context=ctx)
    if not rows:
        raise OdooError("not_found", f"lead {lead_id} does not exist or is not visible to the Odoo user")
    tz = resolve_tz(ctx.get("tz"))
    lead = _format_lead(rows[0], tz, _tag_names(client, rows, ctx), _currency(client, rows), settings.max_body_chars, detail=True)
    out: dict[str, Any] = {"ok": True, "lead": lead}
    if args.get("include_timeline", True):
        limit = clamp_limit(args.get("timeline_limit"), 10)
        entries, untrusted = _timeline_entries(client, settings, ctx, lead_ids=[lead_id], kinds={"messages", "notes", "activities"},
                                               since=None, limit=limit)
        out["timeline"] = entries
        if untrusted:
            out["content_notice"] = UNTRUSTED_NOTICE
    return out


@_guard
def odoo_pipeline_summary(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    group = (args.get("group_by") or "stage").lower()
    groupby_map = {"stage": "stage_id", "salesperson": "user_id", "team": "team_id", "deadline_month": "date_deadline:month",
                   "created_month": "create_date:month", "lost_reason": "lost_reason_id"}
    if group not in groupby_map:
        raise OdooError("bad_argument", f"group_by must be one of {', '.join(groupby_map)}")
    if group == "lost_reason":
        args = {**args, "status": args.get("status") or "lost"}
    domain, extra_ctx, resolved = domains.lead_domain(client, args, default_team=settings.default_team)
    ctx = call_context(client, settings, **extra_ctx)
    lead_fields = client.fields_get("crm.lead")
    groupby = groupby_map[group]
    if groupby.split(":")[0] not in lead_fields:
        raise OdooError("invalid_field", f"this Odoo has no field for group_by={group}")
    sums = [f"{f}:sum" for f in ("expected_revenue", "prorated_revenue") if f in lead_fields]
    try:
        groups = client.read_group("crm.lead", domain, sums, [groupby], context=ctx)
    except OdooError as exc:
        if exc.code != "odoo_error":
            raise
        groups = _client_side_group(client, domain, groupby, ctx, lead_fields)
    currency = _currency(client, [])
    items = []
    total_count = 0
    total_expected = 0.0
    total_weighted = 0.0
    for g in groups:
        raw_key = g.get(groupby)
        key = m2o(raw_key) if isinstance(raw_key, list | tuple) else (raw_key or "(none)")
        count = int(g.get("__count") or g.get(f"{groupby.split(':')[0]}_count") or 0)
        expected = float(g.get("expected_revenue") or 0.0)
        weighted = float(g.get("prorated_revenue") or 0.0)
        total_count += count
        total_expected += expected
        total_weighted += weighted
        items.append({"key": key, "count": count, "expected_revenue": round(expected, 2), "weighted_revenue": round(weighted, 2)})
    if group == "stage":
        order = {s["name"]: i for i, s in enumerate(client.search_read("crm.stage", [], ["name"], order="sequence asc, id asc", context=ctx))}
        items.sort(key=lambda it: order.get(it["key"], 10_000))
    out: dict[str, Any] = {"ok": True, "group_by": group, "currency": currency, "groups": items,
                           "totals": {"count": total_count, "expected_revenue": round(total_expected, 2),
                                      "weighted_revenue": round(total_weighted, 2)}}
    if resolved:
        out["resolved"] = dict(resolved)
    return out


def _client_side_group(client: OdooClient, domain: list[Any], groupby: str, ctx: dict[str, Any],
                       lead_fields: dict[str, Any]) -> list[dict[str, Any]]:
    """Fallback when read_group is unavailable (Odoo 19+ renamed it): aggregate up to 2000 rows locally."""
    field, _, granularity = groupby.partition(":")
    wanted = [f for f in (field, "expected_revenue", "prorated_revenue") if f in lead_fields]
    rows = client.search_read("crm.lead", domain, wanted, limit=2000, context=ctx)
    buckets: dict[Any, dict[str, Any]] = {}
    for r in rows:
        raw = r.get(field)
        key: Any = tuple(raw) if isinstance(raw, list) else raw
        if granularity == "month" and isinstance(raw, str) and len(raw) >= 7:
            key = raw[:7]
        b = buckets.setdefault(key, {groupby: list(key) if isinstance(key, tuple) else key, "__count": 0,
                                     "expected_revenue": 0.0, "prorated_revenue": 0.0})
        b["__count"] += 1
        b["expected_revenue"] += float(r.get("expected_revenue") or 0.0)
        b["prorated_revenue"] += float(r.get("prorated_revenue") or 0.0)
    return list(buckets.values())


@_guard
def odoo_search_partners(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "").strip()
    if not text:
        raise OdooError("bad_argument", "text is required")
    limit = clamp_limit(args.get("limit"), settings.default_limit)
    offset = clamp_offset(args.get("offset"))
    ctx = call_context(client, settings)
    pf = client.fields_get("res.partner")
    domain: list[Any] = domains.text_domain(text, [f for f in ("name", "email", "phone", "mobile", "vat", "ref") if f in pf])
    if args.get("is_company") is not None:
        domain.append(("is_company", "=", bool(args["is_company"])))
    if args.get("city"):
        domain.append(("city", "ilike", str(args["city"])))
    if args.get("country"):
        domain.append(("country_id.name", "ilike", str(args["country"])))
    fields = client.available_fields("res.partner", PARTNER_FIELDS)
    rows = client.search_read("res.partner", domain, fields, limit=limit, offset=offset, order="is_company desc, name asc", context=ctx)
    total = client.search_count("res.partner", domain, context=ctx)
    items = []
    for r in rows:
        item = {
            "id": r.get("id"), "name": r.get("name"), "is_company": bool(r.get("is_company")),
            "company": m2o(r.get("parent_id")), "email": r.get("email") or None, "phone": r.get("phone") or None,
            "mobile": r.get("mobile") or None, "city": r.get("city") or None, "country": m2o(r.get("country_id")),
            "vat": r.get("vat") or None, "salesperson": m2o(r.get("user_id")), "job": r.get("function") or None,
            "opportunity_count": r.get("opportunity_count"),
        }
        items.append({k: v for k, v in item.items() if v is not None})
    return envelope(items, total=total, offset=offset, limit=limit)


def _timeline_entries(client: OdooClient, settings: Settings, ctx: dict[str, Any], *, lead_ids: list[int] | None,
                      partner_ids: list[int] | None = None, kinds: set[str], since: str | None, limit: int) -> tuple[list[dict[str, Any]], bool]:
    tz = resolve_tz(ctx.get("tz"))
    entries: list[tuple[str, dict[str, Any]]] = []
    untrusted = False
    targets: list[tuple[str, list[int]]] = []
    if lead_ids:
        targets.append(("crm.lead", lead_ids))
    if partner_ids:
        targets.append(("res.partner", partner_ids))
    since_dt = f"{since} 00:00:00" if since else None

    if kinds & {"messages", "notes"}:
        mf = client.available_fields("mail.message", MESSAGE_FIELDS)
        for model, ids in targets:
            domain: list[Any] = [("model", "=", model), ("res_id", "in", ids), ("message_type", "in", ["email", "comment"])]
            if since_dt:
                domain.append(("date", ">=", since_dt))
            for m in client.search_read("mail.message", domain, mf, limit=limit * 2, order="date desc", context=ctx):
                is_note = m2o(m.get("subtype_id")) == "Note" or (m.get("message_type") == "comment" and not m.get("email_from"))
                kind = "note" if is_note else "message"
                if (kind == "note" and "notes" not in kinds) or (kind == "message" and "messages" not in kinds):
                    continue
                text = html_to_text(m.get("body"), settings.max_body_chars)
                entry = {"date": fmt_dt(m.get("date"), tz), "kind": kind, "author": m2o(m.get("author_id")) or m.get("email_from"),
                         "subject": m.get("subject") or None, "record": {"model": m.get("model"), "id": m.get("res_id"), "name": m.get("record_name")}}
                if kind == "message":
                    entry["untrusted_text"] = text
                    untrusted = True
                else:
                    entry["text"] = text
                entries.append((m.get("date") or "", {k: v for k, v in entry.items() if v is not None}))

    if "activities" in kinds:
        af = client.available_fields("mail.activity", ACTIVITY_FIELDS)
        for model, ids in targets:
            domain = [("res_model", "=", model), ("res_id", "in", ids)]
            for a in client.search_read("mail.activity", domain, af, limit=limit, order="date_deadline asc", context=ctx):
                entries.append((a.get("date_deadline") or "", {
                    "date": a.get("date_deadline"), "kind": "activity", "state": domains.activity_state(a.get("date_deadline")),
                    "type": m2o(a.get("activity_type_id")), "summary": a.get("summary") or None, "assigned_to": m2o(a.get("user_id")),
                    "note": html_to_text(a.get("note"), settings.max_body_chars) or None,
                    "record": {"model": a.get("res_model"), "id": a.get("res_id"), "name": a.get("res_name")}}))

    if "stage_changes" in kinds and lead_ids:
        tf = client.fields_get("mail.tracking.value")
        if "mail_message_id" in tf:
            msgs = client.search_read("mail.message", [("model", "=", "crm.lead"), ("res_id", "in", lead_ids), ("tracking_value_ids", "!=", False)],
                                      ["id", "date", "author_id", "res_id", "record_name"], limit=limit, order="date desc", context=ctx)
            if msgs:
                fields_t = [f for f in ("mail_message_id", "field_id", "old_value_char", "new_value_char", "field") if f in tf]
                by_msg = {m["id"]: m for m in msgs}
                for t in client.search_read("mail.tracking.value", [("mail_message_id", "in", list(by_msg))], fields_t, limit=limit * 3, context=ctx):
                    label = m2o(t.get("field_id")) or m2o(t.get("field")) or ""
                    if "stage" not in label.lower():
                        continue
                    msg = by_msg.get(m2o_id(t.get("mail_message_id")) or -1, {})
                    entries.append((msg.get("date") or "", {
                        "date": fmt_dt(msg.get("date"), tz), "kind": "stage_change", "author": m2o(msg.get("author_id")),
                        "from": t.get("old_value_char") or None, "to": t.get("new_value_char") or None,
                        "record": {"model": "crm.lead", "id": msg.get("res_id"), "name": msg.get("record_name")}}))

    entries.sort(key=lambda e: e[0], reverse=True)
    return [e[1] for e in entries[:limit]], untrusted


@_guard
def odoo_timeline(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    lead_id, partner_id = args.get("lead_id"), args.get("partner_id")
    if bool(lead_id) == bool(partner_id):
        raise OdooError("bad_argument", "pass exactly one of lead_id or partner_id")
    kinds = set(args.get("kinds") or ["messages", "notes", "activities"])
    limit = clamp_limit(args.get("limit"), 15)
    since = domains.parse_date(args.get("since"), "since")
    ctx = call_context(client, settings, active_test=False)
    if lead_id:
        lead_ids, partner_ids = [int(lead_id)], None
    else:
        partner_ids = [p["id"] for p in client.search_read("res.partner", [("id", "child_of", int(partner_id))], ["id"], limit=200, context=ctx)]
        if not partner_ids:
            raise OdooError("not_found", f"partner {partner_id} does not exist or is not visible")
        lead_ids = [lead["id"] for lead in client.search_read("crm.lead", [("partner_id", "in", partner_ids)], ["id"], limit=200, context=ctx)]
    entries, untrusted = _timeline_entries(client, settings, ctx, lead_ids=lead_ids, partner_ids=partner_ids, kinds=kinds, since=since, limit=limit)
    out: dict[str, Any] = {"ok": True, "count": len(entries), "entries": entries,
                           "note": f"Newest {len(entries)} entries; narrow with since=YYYY-MM-DD or kinds=[...] for more."}
    if untrusted:
        out["content_notice"] = UNTRUSTED_NOTICE
    return out


@_guard
def odoo_list_activities(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    limit = clamp_limit(args.get("limit"), settings.default_limit)
    offset = clamp_offset(args.get("offset"))
    ctx = call_context(client, settings)
    resolved = domains.Resolution()
    domain, window = domains.activity_window(args.get("due"))
    who = args.get("assigned_to") or "me"
    if who.strip().lower() not in ("anyone", "all", "everyone", "*"):
        user_ids = domains.user_ids_for(client, who, resolved=resolved, context=ctx)
        if user_ids:
            domain.append(("user_id", "in", user_ids))
    res_model = args.get("res_model") or "crm.lead"
    if res_model != "any":
        domain.append(("res_model", "=", res_model))
    type_ids = domains.resolve_ids(client, "mail.activity.type", args.get("activity_type"), label="activity_type", resolved=resolved, context=ctx)
    if type_ids:
        domain.append(("activity_type_id", "in", type_ids))
    fields = client.available_fields("mail.activity", ACTIVITY_FIELDS)
    rows = client.search_read("mail.activity", domain, fields, limit=limit, offset=offset, order="date_deadline asc, id asc", context=ctx)
    total = client.search_count("mail.activity", domain, context=ctx)
    today = date.today()
    items = []
    for a in rows:
        item = {"id": a.get("id"), "summary": a.get("summary") or None, "type": m2o(a.get("activity_type_id")),
                "due": a.get("date_deadline"), "state": domains.activity_state(a.get("date_deadline"), today),
                "assigned_to": m2o(a.get("user_id")),
                "record": {"model": a.get("res_model"), "id": a.get("res_id"), "name": a.get("res_name")},
                "note": html_to_text(a.get("note"), settings.max_body_chars) or None}
        items.append({k: v for k, v in item.items() if v is not None})
    extra: dict[str, Any] = {"window": window}
    if resolved:
        extra["resolved"] = dict(resolved)
    return envelope(items, total=total, offset=offset, limit=limit, extra=extra)


@_guard
def odoo_describe_model(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args.get("model") or "").strip()
    if model not in ALLOWED_MODELS:
        raise OdooError("model_not_allowed", f"{model!r} is not allow-listed", "Allowed: " + ", ".join(sorted(ALLOWED_MODELS)))
    needle = str(args.get("filter") or "").lower()
    fields = client.fields_get(model)
    out = {}
    for name, meta in sorted(fields.items()):
        if meta.get("type") in ("binary", "image"):
            continue
        if needle and needle not in name.lower() and needle not in str(meta.get("string", "")).lower():
            continue
        entry: dict[str, Any] = {"label": meta.get("string"), "type": meta.get("type")}
        if meta.get("relation"):
            entry["relation"] = meta["relation"]
        if meta.get("selection"):
            entry["selection"] = [s[0] for s in meta["selection"]][:20]
        if meta.get("store") is False:
            entry["stored"] = False
        out[name] = entry
        if len(out) >= 150:
            break
    return {"ok": True, "model": model, "count": len(out), "fields": out,
            **({"note": "Field list capped at 150; use filter to narrow."} if len(out) >= 150 else {})}


@_guard
def odoo_query(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    model = str(args.get("model") or "").strip()
    if model not in ALLOWED_MODELS:
        raise OdooError("model_not_allowed", f"{model!r} is not allow-listed", "Allowed: " + ", ".join(sorted(ALLOWED_MODELS)))
    fields = list(args.get("fields") or [])
    if not fields or len(fields) > 25:
        raise OdooError("bad_argument", "fields must list 1 to 25 field names")
    meta = client.fields_get(model)
    unknown = [f for f in fields if f.split(".")[0] not in meta]
    if unknown:
        raise OdooError("invalid_field", "unknown fields: " + ", ".join(unknown), "Call odoo_describe_model(model).")
    binary = [f for f in fields if meta.get(f, {}).get("type") in ("binary", "image")]
    if binary:
        raise OdooError("bad_argument", "binary fields cannot be read: " + ", ".join(binary))
    domain = domains.validate_domain(args.get("domain"))
    limit = clamp_limit(args.get("limit"), settings.default_limit)
    offset = clamp_offset(args.get("offset"))
    order = str(args.get("order") or "") or None
    ctx = call_context(client, settings)
    rows = client.search_read(model, domain, fields, limit=limit, offset=offset, order=order, context=ctx)
    total = client.search_count(model, domain, context=ctx)
    tz = resolve_tz(ctx.get("tz"))
    items = []
    for r in rows:
        item = {}
        for k, v in r.items():
            t = meta.get(k, {}).get("type")
            if t == "many2one":
                item[k] = m2o(v)
            elif t in ("one2many", "many2many"):
                item[k] = v or []
            elif t == "datetime":
                item[k] = fmt_dt(v, tz)
            elif t == "html":
                item[k] = html_to_text(v, settings.max_body_chars)
            else:
                item[k] = None if v is False and t not in ("boolean",) else v
        items.append(item)
    return envelope(items, total=total, offset=offset, limit=limit, extra={"model": model})


# ---- write tools (opt-in, human-approved) -------------------------------------------------------

WRITE_TOOL_NAMES = ("odoo_log_note", "odoo_schedule_activity", "odoo_update_lead_stage")
_MAX_NOTE_CHARS = 4000
_lead_model_id: dict[str, tuple[float, int]] = {}


def _require_write(settings: Settings) -> None:
    if not settings.write_enabled:
        raise OdooError("writes_disabled", "Odoo writes are disabled for this assistant",
                        "The operator can enable them with write_enabled: true (Desktop plugin settings or "
                        "`hermes config set plugins.entries.alfred-odoo.settings.write_enabled true`).")


def _load_lead(client: OdooClient, settings: Settings, args: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The target lead (name, stage, team) or ``not_found``; also returns the call context."""
    try:
        lead_id = int(args.get("lead_id"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise OdooError("bad_argument", "lead_id (integer) is required") from None
    ctx = call_context(client, settings, active_test=False)
    fields = client.available_fields("crm.lead", ["id", "name", "stage_id", "team_id", "user_id", "active"])
    rows = client.read("crm.lead", [lead_id], fields, context=ctx)
    if not rows:
        raise OdooError("not_found", f"lead {lead_id} does not exist or is not visible to the Odoo user")
    return rows[0], ctx


def _note_html(text: str) -> str:
    return "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"


def _first_id(value: Any) -> int | None:
    """``message_post`` returns the message id (JSON-RPC serialises the recordset as id or [id])."""
    if isinstance(value, list | tuple):
        return int(value[0]) if value else None
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _crm_lead_model_id(client: OdooClient) -> int:
    key = f"{client.url}|{client.db}"
    cached = _lead_model_id.get(key)
    if cached and time.time() - cached[0] < _VOCAB_TTL:
        return cached[1]
    rows = client.search_read("ir.model", [("model", "=", "crm.lead")], ["id"], limit=1)
    if not rows:
        raise OdooError("model_not_installed", "crm.lead is not installed on this Odoo")
    _lead_model_id[key] = (time.time(), int(rows[0]["id"]))
    return _lead_model_id[key][1]


@_guard
def odoo_log_note(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(settings)
    note = str(args.get("note") or "").strip()
    if not note:
        raise OdooError("bad_argument", "note is required")
    if len(note) > _MAX_NOTE_CHARS:
        raise OdooError("bad_argument", f"note is longer than {_MAX_NOTE_CHARS} characters")
    lead, ctx = _load_lead(client, settings, args)
    kwargs: dict[str, Any] = {"body": _note_html(note), "message_type": "comment", "subtype_xmlid": "mail.mt_note", "context": ctx}
    if client.server_major >= 17:  # 17+ escapes a str body unless told it is HTML; 16 treats it as HTML
        kwargs["body_is_html"] = True
    result = client.write_kw("crm.lead", "message_post", [[lead["id"]]], kwargs)
    return {"ok": True, "lead": {"id": lead["id"], "name": lead.get("name")}, "message_id": _first_id(result), "note": note}


@_guard
def odoo_schedule_activity(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(settings)
    summary = str(args.get("summary") or "").strip()
    if not summary:
        raise OdooError("bad_argument", "summary is required")
    lead, ctx = _load_lead(client, settings, args)
    resolved = domains.Resolution()
    deadline = domains.parse_date(args.get("deadline") or "today", "deadline")
    type_domain = ["|", ("res_model", "=", False), ("res_model", "=", "crm.lead")]
    if args.get("activity_type"):
        type_id = domains.resolve_one(client, "mail.activity.type", str(args["activity_type"]), domain=type_domain,
                                      label="activity_type", resolved=resolved, context=ctx)
    else:
        types = client.search_read("mail.activity.type", type_domain, ["id", "name"], order="sequence asc, id asc", limit=1, context=ctx)
        if not types:
            raise OdooError("no_match", "this Odoo has no activity type for leads", "Create one in Odoo (Settings > Activity types).")
        type_id, resolved["activity_type"] = int(types[0]["id"]), types[0]["name"]
    user_ids = domains.user_ids_for(client, str(args.get("assign_to") or "me"), resolved=resolved, context=ctx) or []
    if len(user_ids) != 1:
        raise OdooError("ambiguous", f"assign_to matches several users: {resolved.get('salesperson')}",
                        hint="Repeat the call with the exact user name.")
    values: dict[str, Any] = {"res_model_id": _crm_lead_model_id(client), "res_id": lead["id"], "activity_type_id": type_id,
                              "summary": summary, "date_deadline": deadline, "user_id": user_ids[0]}
    note = str(args.get("note") or "").strip()
    if note:
        values["note"] = _note_html(note[:_MAX_NOTE_CHARS])
    activity_id = client.write_kw("mail.activity", "create", [values], {"context": ctx})
    return {"ok": True, "lead": {"id": lead["id"], "name": lead.get("name")},
            "activity": {"id": _first_id(activity_id), "summary": summary, "type": resolved.get("activity_type"),
                         "deadline": deadline, "assigned_to": resolved.get("salesperson")}}


@_guard
def odoo_update_lead_stage(client: OdooClient, settings: Settings, args: dict[str, Any]) -> dict[str, Any]:
    _require_write(settings)
    stage_text = str(args.get("stage") or "").strip()
    if not stage_text:
        raise OdooError("bad_argument", "stage is required")
    lead, ctx = _load_lead(client, settings, args)
    resolved = domains.Resolution()
    stage_domain: list[Any] | None = None
    if "team_id" in client.fields_get("crm.stage"):
        team_id = m2o_id(lead.get("team_id"))
        stage_domain = ["|", ("team_id", "=", False), ("team_id", "=", team_id)] if team_id else [("team_id", "=", False)]
    stage_id = domains.resolve_one(client, "crm.stage", stage_text, domain=stage_domain, label="stage", resolved=resolved, context=ctx)
    stage_rows = client.read("crm.stage", [stage_id], client.available_fields("crm.stage", ["name", "is_won"]), context=ctx)
    stage = stage_rows[0] if stage_rows else {"name": resolved.get("stage")}
    out = {"ok": True, "lead": {"id": lead["id"], "name": lead.get("name")}, "from_stage": m2o(lead.get("stage_id")),
           "to_stage": stage.get("name"), "is_won": bool(stage.get("is_won"))}
    if m2o_id(lead.get("stage_id")) == stage_id:
        return {**out, "changed": False, "note": "The lead is already in that stage; nothing was written."}
    client.write_kw("crm.lead", "write", [[lead["id"]], {"stage_id": stage_id}], {"context": ctx})
    return {**out, "changed": True}


def ping(client: OdooClient, settings: Settings | None = None) -> dict[str, Any]:
    """Used by `hermes odoo test`: authenticate and describe what the connection can see."""
    version = client.version()
    user = client.user_context()
    out: dict[str, Any] = {"url": client.url, "db": client.db, "odoo_version": version.get("server_version"),
                           "user": user.get("name"), "uid": client.uid, "tz": user.get("tz"), "lang": user.get("lang"),
                           "company_ids": user.get("company_ids")}
    if settings is not None:
        out["write_enabled"] = settings.write_enabled
        out["write_tools"] = list(WRITE_TOOL_NAMES) if settings.write_enabled else []
    try:
        out["open_opportunities"] = client.search_count("crm.lead", [("type", "=", "opportunity")])
        out["crm_installed"] = True
    except OdooError as exc:
        out["crm_installed"] = False
        out["crm_error"] = exc.to_dict()
    return out


def as_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
