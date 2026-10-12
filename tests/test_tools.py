import json

import pytest
from alfred_odoo import tools
from alfred_odoo.humanize import UNTRUSTED_NOTICE

READ_TOOLS = [
    tools.odoo_crm_vocabulary, tools.odoo_search_leads, tools.odoo_get_lead, tools.odoo_pipeline_summary,
    tools.odoo_search_partners, tools.odoo_timeline, tools.odoo_list_activities, tools.odoo_describe_model, tools.odoo_query,
]
WRITE_TOOLS = [tools.odoo_log_note, tools.odoo_schedule_activity, tools.odoo_update_lead_stage]
ALL_TOOLS = READ_TOOLS + WRITE_TOOLS
MINIMAL_ARGS = {
    tools.odoo_get_lead: {"lead_id": 10},
    tools.odoo_search_partners: {"text": "acme"},
    tools.odoo_timeline: {"lead_id": 10},
    tools.odoo_describe_model: {"model": "crm.lead"},
    tools.odoo_query: {"model": "crm.lead", "fields": ["name"]},
    tools.odoo_log_note: {"lead_id": 10, "note": "Called the client"},
    tools.odoo_schedule_activity: {"lead_id": 10, "summary": "Follow-up call"},
    tools.odoo_update_lead_stage: {"lead_id": 10, "stage": "Proposition"},
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
def test_every_tool_succeeds_against_the_fake(fn, configured_writes):
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


def test_api_key_never_appears_in_any_tool_output(configured_writes):
    for fn in ALL_TOOLS:
        assert "test-key" not in fn(MINIMAL_ARGS.get(fn, {}))
    configured_writes.fail_next = {"data": {"name": "X", "message": "key test-key is bad"}}
    assert "test-key" not in tools.odoo_search_leads({})
    configured_writes.fail_next = {"data": {"name": "odoo.exceptions.UserError", "message": "key test-key is bad"}}
    assert "test-key" not in tools.odoo_log_note({"lead_id": 10, "note": "x"})


def test_ping(configured):
    from alfred_odoo.settings import load_settings, make_client

    info = tools.ping(make_client(), load_settings())
    assert info["crm_installed"] is True and info["user"] == "Marc Demo" and info["odoo_version"] == "17.0"
    assert info["write_enabled"] is False and info["write_tools"] == []


# ---- write tools --------------------------------------------------------------------------------


@pytest.mark.parametrize("fn", WRITE_TOOLS)
def test_write_tools_refuse_when_writes_are_disabled(fn, configured):
    out = call(fn, **MINIMAL_ARGS[fn])
    assert out["ok"] is False and out["error"]["code"] == "writes_disabled"
    assert configured.writes == []


@pytest.mark.parametrize("fn", WRITE_TOOLS)
def test_write_tools_report_not_found_without_writing(fn, configured_writes):
    out = call(fn, **{**MINIMAL_ARGS[fn], "lead_id": 999})
    assert out["ok"] is False and out["error"]["code"] == "not_found"
    assert configured_writes.writes == []


def test_log_note_posts_escaped_html_note(configured_writes):
    out = call(tools.odoo_log_note, lead_id=10, note="Called <John>\nHe said: a & b")
    assert out["ok"] and out["lead"] == {"id": 10, "name": "ACME – 50 seats"} and out["message_id"] == 123
    (model, method, args, kwargs), = configured_writes.writes
    assert (model, method, args) == ("crm.lead", "message_post", [[10]])
    assert kwargs["body"] == "<p>Called &lt;John&gt;<br>He said: a &amp; b</p>"
    assert kwargs["message_type"] == "comment" and kwargs["subtype_xmlid"] == "mail.mt_note"
    assert kwargs["body_is_html"] is True and kwargs["context"]["tz"] == "Europe/Rome"


def test_log_note_on_odoo_16_sends_html_without_the_17_flag(configured, monkeypatch):
    from alfred_odoo import client as client_mod
    from alfred_odoo import settings as settings_mod
    from conftest import ALL_AREAS, FakeCtx, FakeOdoo

    fake16 = FakeOdoo(version=16)
    monkeypatch.setattr(client_mod, "default_transport", fake16)
    settings_mod.bind_context(FakeCtx({"write_enabled": True, "enabled_areas": ALL_AREAS}))
    assert call(tools.odoo_log_note, lead_id=10, note="hi")["ok"]
    kwargs = fake16.writes[0][3]
    assert kwargs["body"] == "<p>hi</p>" and "body_is_html" not in kwargs


def test_log_note_validates_text(configured_writes):
    assert call(tools.odoo_log_note, lead_id=10, note="  ")["error"]["code"] == "bad_argument"
    assert call(tools.odoo_log_note, lead_id=10, note="x" * 4001)["error"]["code"] == "bad_argument"
    assert call(tools.odoo_log_note, lead_id="abc", note="x")["error"]["code"] == "bad_argument"
    assert configured_writes.writes == []


def test_schedule_activity_defaults(configured_writes):
    out = call(tools.odoo_schedule_activity, lead_id=10, summary="Follow-up call", deadline="2026-10-15")
    assert out["ok"] and out["activity"] == {"id": 77, "summary": "Follow-up call", "type": "Call", "deadline": "2026-10-15",
                                             "assigned_to": "Marc Demo"}
    (model, method, args, kwargs), = configured_writes.writes
    assert (model, method) == ("mail.activity", "create")
    assert args[0] == {"res_model_id": 99, "res_id": 10, "activity_type_id": 1, "summary": "Follow-up call",
                       "date_deadline": "2026-10-15", "user_id": 2}
    assert "note" not in args[0]


def test_schedule_activity_resolves_type_and_user_and_note(configured_writes):
    out = call(tools.odoo_schedule_activity, lead_id=10, summary="Demo", activity_type="call", assign_to="alice", note="bring slides")
    assert out["ok"] and out["activity"]["assigned_to"] == "Alice" and out["activity"]["type"] == "Call"
    values = configured_writes.writes[0][2][0]
    assert values["user_id"] == 3 and values["activity_type_id"] == 1 and values["note"] == "<p>bring slides</p>"
    assert call(tools.odoo_schedule_activity, lead_id=10, summary="x", deadline="tomorrow")["error"]["code"] == "bad_argument"
    assert call(tools.odoo_schedule_activity, lead_id=10, summary="x", note="n" * 4001)["error"]["code"] == "bad_argument"
    assert len(configured_writes.writes) == 1  # the oversized note was refused, not truncated
    assert call(tools.odoo_schedule_activity, lead_id=10, summary="x", activity_type="zzz")["error"]["code"] == "no_match"
    assert call(tools.odoo_schedule_activity, lead_id=10, summary="x", assign_to="zzz")["error"]["code"] == "no_match"


def test_update_lead_stage_writes_resolved_stage(configured_writes):
    out = call(tools.odoo_update_lead_stage, lead_id=10, stage="proposition")
    assert out == {"ok": True, "lead": {"id": 10, "name": "ACME – 50 seats"}, "from_stage": "Qualified",
                   "to_stage": "Proposition", "is_won": False, "changed": True}
    (model, method, args, kwargs), = configured_writes.writes
    assert (model, method, args) == ("crm.lead", "write", [[10], {"stage_id": 3}])
    # now a no-op
    again = call(tools.odoo_update_lead_stage, lead_id=10, stage="Proposition")
    assert again["ok"] and again["changed"] is False and len(configured_writes.writes) == 1


def test_update_lead_stage_to_won_and_ambiguity(configured_writes):
    out = call(tools.odoo_update_lead_stage, lead_id=11, stage="won")
    assert out["ok"] and out["is_won"] is True and out["from_stage"] == "Proposition"
    out = call(tools.odoo_update_lead_stage, lead_id=10, stage="prop")  # Proposition + "Proposal sent"
    assert out["ok"] is False and out["error"]["code"] == "ambiguous" and "Proposal sent" in out["error"]["message"]
    assert call(tools.odoo_update_lead_stage, lead_id=10, stage="zzz")["error"]["code"] == "no_match"
    assert len(configured_writes.writes) == 1


def test_write_tools_map_odoo_refusal(configured_writes):
    configured_writes.fail_next = {"data": {"name": "odoo.exceptions.UserError", "message": "You cannot do that"}}
    out = call(tools.odoo_update_lead_stage, lead_id=10, stage="won")
    assert out["ok"] is False and out["error"]["code"] == "validation_error"
