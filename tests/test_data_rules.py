"""Per-customer data rules from settings: a plausible revenue range and extra opportunity fields."""

import json

import pytest
from alfred_odoo import settings as settings_mod
from alfred_odoo import tools
from alfred_odoo.client import OdooError
from alfred_odoo.settings import load_settings, make_client, parse_custom_fields, parse_revenue_range
from conftest import ALL_AREAS, FakeCtx


def call(fn, **args):
    return json.loads(fn(args, session_id="s1", task_id="t1"))


def with_config(**config):
    settings_mod.bind_context(FakeCtx({"enabled_areas": ALL_AREAS, **config}))


@pytest.fixture
def kimera(configured):
    """Two plausible cars, one typo (880) and one empty amount; a many2one model and a many2many tag field."""
    meta = {**configured.fields["crm.lead"],
            "x_studio_modello_auto_1": {"string": "Modello", "type": "many2one", "relation": "account.analytic.account"},
            "x_studio_modelli_auto": {"string": "Modelli", "type": "many2many", "relation": "account.analytic.account"}}
    configured.fields = {**configured.fields, "crm.lead": meta}
    base = configured.leads[0]
    configured.leads = [
        {**base, "id": 1, "name": "EVO 38 Rossi", "expected_revenue": 880000.0, "prorated_revenue": 440000.0,
         "x_studio_modello_auto_1": [1, "EVO 38"]},
        {**base, "id": 2, "name": "K39 Bianchi", "expected_revenue": 1780000.0, "prorated_revenue": 890000.0,
         "x_studio_modello_auto_1": [2, "K39 Stradale"]},
        {**base, "id": 3, "name": "EVO 38 typo", "expected_revenue": 880.0, "prorated_revenue": 440.0,
         "x_studio_modello_auto_1": [1, "EVO 38"]},
        {**base, "id": 4, "name": "Old lead", "expected_revenue": 0.0, "prorated_revenue": 0.0,
         "x_studio_modello_auto_1": False},
    ]
    return configured


def test_parse_revenue_range():
    assert parse_revenue_range([]) is None and parse_revenue_range("") is None
    assert parse_revenue_range("100000,2000000") == (100000.0, 2000000.0)
    assert parse_revenue_range([100000, 0]) == (100000.0, float("inf"))
    for bad in ([1], [5, 2], [-1, 10]):
        with pytest.raises(OdooError):
            parse_revenue_range(bad)


def test_parse_custom_fields():
    assert parse_custom_fields("Model=x_studio_modello_auto_1") == (("model", "x_studio_modello_auto_1"),)
    assert parse_custom_fields([]) == ()
    with pytest.raises(OdooError):
        parse_custom_fields(["model"])


def test_malformed_setting_keeps_tools_visible_but_fails_calls(configured):
    with_config(revenue_range=[5])
    assert load_settings().config_errors
    out = call(tools.odoo_search_leads)
    assert out["ok"] is False and out["error"]["code"] == "bad_config"


def test_pipeline_without_range_keeps_server_side_sums(configured):
    with_config()
    out = call(tools.odoo_pipeline_summary)
    assert out["totals"]["expected_revenue"] == 15000.0 and "suspect_amounts" not in out


def test_pipeline_excludes_implausible_amounts_but_counts_them(kimera):
    with_config(revenue_range="100000,2000000", custom_fields="model=x_studio_modello_auto_1")
    out = call(tools.odoo_pipeline_summary, group_by="model")
    assert out["totals"] == {"count": 4, "expected_revenue": 2660000.0, "weighted_revenue": 1330000.0}
    groups = {g["key"]: g for g in out["groups"]}
    assert groups["EVO 38"]["count"] == 2 and groups["EVO 38"]["expected_revenue"] == 880000.0
    assert groups["EVO 38"]["suspect_amounts"] == 1
    assert groups["(none)"]["count"] == 1  # empty model, and an empty amount is not suspect
    sus = out["suspect_amounts"]
    assert sus["count"] == 1 and sus["leads"] == [{"id": 3, "name": "EVO 38 typo", "expected_revenue": 880.0}]
    assert sus["range"] == [100000.0, 2000000.0]
    assert not any(m == "crm.lead" and om == "read_group" for _s, _m, m, om in kimera.calls)


def test_leads_show_custom_fields_and_flag_suspect_amounts(kimera):
    with_config(revenue_range="100000,2000000", custom_fields="model=x_studio_modello_auto_1")
    items = {i["id"]: i for i in call(tools.odoo_search_leads)["items"]}
    assert items[1]["custom_fields"] == {"model": "EVO 38"} and "suspect" not in items[1]["expected_revenue"]
    assert items[3]["expected_revenue"]["suspect"] is True
    assert "custom_fields" not in items[4]
    lead = call(tools.odoo_get_lead, lead_id=3, include_timeline=False)["lead"]
    assert lead["custom_fields"] == {"model": "EVO 38"} and lead["expected_revenue"]["suspect"] is True


def test_x2many_and_missing_custom_fields_are_rejected(kimera):
    with_config(custom_fields="interest=x_studio_modelli_auto,ghost=x_nope,stage=x_studio_modello_auto_1")
    out = call(tools.odoo_pipeline_summary, group_by="interest")
    assert out["error"]["code"] == "bad_argument" and "many2many" in out["error"]["message"]
    vocab = call(tools.odoo_crm_vocabulary)
    assert set(vocab["custom_fields_rejected"]) == {"interest", "ghost", "stage"}


def test_vocabulary_and_ping_report_the_rules(kimera):
    with_config(revenue_range="100000,0", custom_fields="model=x_studio_modello_auto_1")
    vocab = call(tools.odoo_crm_vocabulary)
    assert vocab["custom_fields"] == ["model"] and vocab["plausible_revenue_range"] == [100000.0, None]
    info = tools.ping(make_client(), load_settings())
    assert info["custom_fields"] == {"model": "x_studio_modello_auto_1"} and info["revenue_range"] == [100000.0, None]
