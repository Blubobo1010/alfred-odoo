import json

import pytest
from alfred_odoo import tools
from alfred_odoo.humanize import UNTRUSTED_NOTICE

ALL_TOOLS = [
    tools.odoo_crm_vocabulary, tools.odoo_search_leads, tools.odoo_get_lead, tools.odoo_pipeline_summary,
    tools.odoo_search_partners, tools.odoo_timeline, tools.odoo_list_activities, tools.odoo_describe_model, tools.odoo_query,
]
MINIMAL_ARGS = {
    tools.odoo_get_lead: {"lead_id": 10},
    tools.odoo_search_partners: {"text": "acme"},
    tools.odoo_timeline: {"lead_id": 10},
    tools.odoo_describe_model: {"model": "crm.lead"},
    tools.odoo_query: {"model": "crm.lead", "fields": ["name"]},
}


def call(fn, **args):
    return json.loads(fn(args, session_id="s1", task_id="t1"))


@pytest.mark.parametrize("fn", ALL_TOOLS)
def test_every_tool_returns_json_and_never_raises_without_credentials(fn, monkeypatch):
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    out = call(fn, **MINIMAL_ARGS.get(fn, {}))
    assert out["ok"] is False and out["error"]["code"] == "not_configured"


@pytest.mark.parametrize("fn", ALL_TOOLS)
def test_every_tool_succeeds_against_the_fake(fn, configured):
    out = call(fn, **MINIMAL_ARGS.get(fn, {}))
    assert out["ok"] is True, out


def test_search_leads_shape(configured):
    out = call(tools.odoo_search_leads, stage="qualif", salesperson="me", limit=1)
    assert out["total"] == 2 and out["count"] == 1 and out["has_more"] is True and out["next_offset"] == 1
    lead = out["items"][0]
    assert lead["name"] == "ACME – 50 seats"
    assert lead["partner"] == {"id": 7, "name": "ACME Corp"}
    assert lead["stage"] == "Qualified" and lead["salesperson"] == "Marc Demo"
    assert lead["expected_revenue"] == {"amount": 12000.0, "currency": "EUR"}
    assert lead["tags"] == ["Hot"] and lead["priority"] == "high" and lead["status"] == "open"
    assert lead["created"] == "2026-10-01T11:30+02:00"  # user tz Europe/Rome
    assert out["resolved"] == {"stage": "Qualified", "salesperson": "Marc Demo"}
    assert "description" not in lead


def test_search_leads_bad_name_returns_hint_not_exception(configured):
    out = call(tools.odoo_search_leads, stage="zzz")
    assert out["ok"] is False and out["error"]["code"] == "no_match" and "Qualified" in out["error"]["hint"]


def test_get_lead_detail_and_timeline_marks_untrusted(configured):
    out = call(tools.odoo_get_lead, lead_id=10)
    lead = out["lead"]
    assert lead["description"] == "Hello world"  # html stripped, quoted history dropped
    assert lead["next_activity"] == {"summary": "Call back", "due": "2026-10-12"}
    kinds = [e["kind"] for e in out["timeline"]]
    assert kinds == ["message", "note", "activity"] or set(kinds) == {"message", "note", "activity"}
    message = next(e for e in out["timeline"] if e["kind"] == "message")
    assert "untrusted_text" in message and "text" not in message
    assert out["content_notice"] == UNTRUSTED_NOTICE


def test_get_lead_not_found(configured):
    out = call(tools.odoo_get_lead, lead_id=999)
    assert out["ok"] is False and out["error"]["code"] == "not_found"


def test_pipeline_summary_orders_stages(configured):
    out = call(tools.odoo_pipeline_summary)
    assert [g["key"] for g in out["groups"]] == ["Qualified", "Proposition"]
    assert out["totals"] == {"count": 2, "expected_revenue": 15000.0, "weighted_revenue": 8400.0}
    assert out["currency"] == "EUR"


def test_pipeline_summary_rejects_bad_group(configured):
    assert call(tools.odoo_pipeline_summary, group_by="colour")["error"]["code"] == "bad_argument"


def test_search_partners(configured):
    out = call(tools.odoo_search_partners, text="acme")
    assert out["items"][0]["name"] == "ACME Corp" and out["items"][0]["country"] == "United States"
    assert call(tools.odoo_search_partners, text=" ")["error"]["code"] == "bad_argument"


def test_timeline_requires_exactly_one_target(configured):
    assert call(tools.odoo_timeline)["error"]["code"] == "bad_argument"
    assert call(tools.odoo_timeline, lead_id=10, partner_id=7)["error"]["code"] == "bad_argument"
    out = call(tools.odoo_timeline, lead_id=10, kinds=["notes"])
    assert [e["kind"] for e in out["entries"]] == ["note"] and "content_notice" not in out


def test_list_activities_defaults_to_me(configured):
    out = call(tools.odoo_list_activities)
    assert out["window"] == "overdue_and_today" and out["resolved"]["salesperson"] == "Marc Demo"
    assert out["items"][0]["record"] == {"model": "crm.lead", "id": 10, "name": "ACME – 50 seats"}
    assert out["items"][0]["state"] in ("overdue", "today", "planned")


def test_describe_model_hides_binary_and_filters(configured):
    out = call(tools.odoo_describe_model, model="res.partner", filter="mail")
    assert "email" in out["fields"] and "image_128" not in out["fields"]
    assert call(tools.odoo_describe_model, model="sale.order")["error"]["code"] == "model_not_allowed"


def test_query_validates_fields_and_domain(configured):
    assert call(tools.odoo_query, model="crm.lead", fields=["nope"])["error"]["code"] == "invalid_field"
    assert call(tools.odoo_query, model="crm.lead", fields=["name"], domain=[["name", "regexp", "x"]])["error"]["code"] == "bad_argument"
    out = call(tools.odoo_query, model="crm.lead", fields=["name", "stage_id", "create_date"], domain=[["name", "ilike", "acme"]])
    assert out["ok"] and out["items"][0]["stage_id"] == "Qualified" and out["items"][0]["create_date"].startswith("2026-10-01T11:30")


def test_api_key_never_appears_in_any_tool_output(configured):
    for fn in ALL_TOOLS:
        assert "test-key" not in fn(MINIMAL_ARGS.get(fn, {}))
    configured.fail_next = {"data": {"name": "X", "message": "key test-key is bad"}}
    assert "test-key" not in tools.odoo_search_leads({})


def test_ping(configured):
    from alfred_odoo.settings import make_client

    info = tools.ping(make_client())
    assert info["crm_installed"] is True and info["user"] == "Marc Demo" and info["odoo_version"] == "17.0"
