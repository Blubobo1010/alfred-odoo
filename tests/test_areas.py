"""Data areas are default-deny: nothing of an area is read or written until the operator enables it."""

import json

import alfred_odoo
import pytest
from alfred_odoo import settings as settings_mod
from alfred_odoo import tools
from alfred_odoo.client import AREA_MODELS, META_MODELS, OdooClient, normalize_areas
from conftest import ALL_AREAS, FakeCtx

AREA_MODEL_NAMES = {m for models in AREA_MODELS.values() for m in models}


def call(fn, **args):
    return json.loads(fn(args, session_id="s1", task_id="t1"))


def with_areas(*areas, **extra):
    settings_mod.bind_context(FakeCtx({"enabled_areas": list(areas), **extra}))


def models_called(fake):
    return {model for _service, _method, model, odoo_method in fake.calls if model and odoo_method != "fields_get"}


def test_every_model_belongs_to_exactly_one_area_or_is_metadata():
    seen = [m for models in AREA_MODELS.values() for m in models]
    assert len(seen) == len(set(seen))
    assert not (set(seen) & META_MODELS)


def test_normalize_areas_accepts_lists_and_comma_strings():
    assert normalize_areas(["crm", " Contacts "]) == (frozenset({"crm", "contacts"}), ())
    assert normalize_areas("crm, chatter,invoices") == (frozenset({"crm", "chatter"}), ("invoices",))
    assert normalize_areas(None) == (frozenset(), ())


def test_out_of_the_box_every_tool_is_hidden(configured):
    settings_mod.bind_context(FakeCtx({"advanced_tools": True, "write_enabled": True}))
    ctx = FakeCtx({"advanced_tools": True, "write_enabled": True})
    alfred_odoo.register(ctx)
    assert {name: t["check_fn"]() for name, t in ctx.tools.items()} == {name: False for name in ctx.tools}


def test_out_of_the_box_tools_read_no_record(configured):
    settings_mod.bind_context(FakeCtx({}))
    out = call(tools.odoo_search_leads)
    assert out["ok"] is False and out["error"]["code"] == "area_not_enabled"
    assert "not available" in out["error"]["hint"]
    assert not (models_called(configured) & AREA_MODEL_NAMES)


@pytest.mark.parametrize("model", sorted(AREA_MODEL_NAMES))
def test_client_refuses_records_of_a_disabled_area_before_calling_odoo(fake, model):
    client = OdooClient("https://odoo.test", "demo", "admin", "test-key", areas=(), transport=fake)
    with pytest.raises(Exception) as exc:
        client.search_read(model, [], ["id"])
    assert getattr(exc.value, "code", None) == "area_not_enabled"
    assert model not in models_called(fake)


def test_schema_and_metadata_stay_readable_with_no_area(fake):
    client = OdooClient("https://odoo.test", "demo", "admin", "test-key", areas=(), transport=fake)
    assert "name" in client.fields_get("crm.lead")  # the operator inspects an area before enabling it
    assert client.user_context()["name"] == "Marc Demo"  # res.users is session metadata


def test_writes_refused_in_a_disabled_area(fake):
    client = OdooClient("https://odoo.test", "demo", "admin", "test-key", areas=("crm",), transport=fake)
    with pytest.raises(Exception) as exc:
        client.write_kw("mail.activity", "create", [{"summary": "x"}])
    assert getattr(exc.value, "code", None) == "area_not_enabled"
    assert fake.writes == []


def test_tool_visibility_follows_the_areas(configured):
    with_areas("crm")
    ctx = FakeCtx({"enabled_areas": ["crm"]})
    alfred_odoo.register(ctx)
    visible = {name for name, t in ctx.tools.items() if t["check_fn"]()}
    assert visible == {"odoo_crm_vocabulary", "odoo_search_leads", "odoo_get_lead", "odoo_pipeline_summary"}
    ctx.config.update({"enabled_areas": ALL_AREAS, "write_enabled": True})
    assert ctx.tools["odoo_schedule_activity"]["check_fn"]() is True
    ctx.config["enabled_areas"] = ["crm"]
    assert ctx.tools["odoo_schedule_activity"]["check_fn"]() is False  # needs activities too
    assert ctx.tools["odoo_log_note"]["check_fn"]() is True


def test_get_lead_with_crm_only_skips_the_timeline_and_says_so(configured):
    with_areas("crm")
    out = call(tools.odoo_get_lead, lead_id=10)
    assert out["ok"] is True and out["lead"]["name"] == "ACME – 50 seats"
    assert "timeline" not in out and out["areas_not_enabled"] == ["activities", "chatter"]
    assert not (models_called(configured) & (AREA_MODELS["chatter"] | AREA_MODELS["activities"]))


def test_vocabulary_without_activities_omits_activity_types(configured):
    with_areas("crm")
    out = call(tools.odoo_crm_vocabulary)
    assert out["ok"] is True and "stages" in out and "activity_types" not in out
    assert "mail.activity.type" not in models_called(configured)


def test_timeline_keeps_only_enabled_kinds(configured):
    with_areas("crm", "activities")
    out = call(tools.odoo_timeline, lead_id=10)
    assert out["ok"] is True and {e["kind"] for e in out["entries"]} == {"activity"}
    assert out["areas_not_enabled"] == ["chatter"]
    assert "mail.message" not in models_called(configured)


def test_timeline_of_a_lead_needs_crm(configured):
    with_areas("chatter")
    out = call(tools.odoo_timeline, lead_id=10)
    assert out["ok"] is False and out["error"]["code"] == "area_not_enabled"


def test_timeline_with_no_history_area_is_refused(configured):
    with_areas("crm")
    out = call(tools.odoo_timeline, lead_id=10)
    assert out["ok"] is False and out["error"]["code"] == "area_not_enabled"


def test_partner_timeline_without_crm_reads_only_partner_history(configured):
    with_areas("contacts", "chatter")
    out = call(tools.odoo_timeline, partner_id=7)
    assert out["ok"] is True and "crm" in out["areas_not_enabled"]
    assert "crm.lead" not in models_called(configured)


def test_query_on_a_disabled_area_is_refused(configured):
    with_areas("crm", advanced_tools=True)
    out = call(tools.odoo_query, model="res.partner", fields=["name"])
    assert out["ok"] is False and out["error"]["code"] == "area_not_enabled"


def test_ping_reports_areas_without_reading_crm_records(configured):
    settings_mod.bind_context(FakeCtx({"enabled_areas": "contacts,invoices"}))
    from alfred_odoo.settings import load_settings, make_client

    info = tools.ping(make_client(), load_settings())
    assert info["crm_installed"] is True and "open_opportunities" not in info
    assert info["enabled_areas"] == ["contacts"] and info["unknown_areas"] == ["invoices"]
    assert "crm" in info["disabled_areas"]
    assert "crm.lead" not in models_called(configured)


def test_ping_with_no_area_explains_how_to_enable(configured):
    settings_mod.bind_context(FakeCtx({}))
    from alfred_odoo.settings import load_settings, make_client

    info = tools.ping(make_client(), load_settings())
    assert info["enabled_areas"] == [] and "enabled_areas" in info["note"]
