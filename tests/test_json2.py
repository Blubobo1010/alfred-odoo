"""The JSON-2 wire (Odoo 18 Online / 19+): bearer key, named arguments, HTTP status errors."""

import json

import pytest
from alfred_odoo import client as client_mod
from alfred_odoo import settings as settings_mod
from alfred_odoo import tools
from alfred_odoo.client import AREAS, OdooClient, OdooError, json2_body, major_version
from alfred_odoo.settings import Settings, resolve_protocol
from conftest import FakeCtx, FakeOdoo


def j2(fake, *, db="", username="", areas=AREAS):
    return OdooClient("https://odoo.test", db, username, "test-key", areas=areas, protocol="json2",
                      transport=lambda *a: pytest.fail("JSON-RPC must not be used"), json2_transport=fake.json2)


def test_request_shape_named_arguments_and_bearer(fake):
    c = j2(fake)
    c.search_read("crm.lead", [("type", "=", "opportunity")], ["name"], limit=5, order="id desc")
    url, body, headers = fake.last_json2
    assert url == "https://odoo.test/json/2/crm.lead/search_read"
    assert body == {"domain": [("type", "=", "opportunity")],
                    "fields": ["name"], "offset": 0, "limit": 5, "order": "id desc"}
    assert headers["Authorization"] == "bearer test-key" and "X-Odoo-Database" not in headers


def test_database_header_only_when_set(fake):
    j2(fake, db="kimera").search_count("crm.lead", [])
    assert fake.last_json2[2]["X-Odoo-Database"] == "kimera"


@pytest.mark.parametrize(("method", "args", "kwargs", "expected"), [
    ("read", [[1, 2]], {"fields": ["name"]}, {"ids": [1, 2], "fields": ["name"]}),
    ("write", [[10], {"stage_id": 3}], {}, {"ids": [10], "vals": {"stage_id": 3}}),
    ("create", [{"summary": "x"}], {}, {"vals_list": [{"summary": "x"}]}),
    ("message_post", [[10]], {"body": "<p>x</p>"}, {"ids": [10], "body": "<p>x</p>"}),
    ("fields_get", [], {"attributes": ["type"]}, {"attributes": ["type"]}),
])
def test_positional_arguments_get_their_names(method, args, kwargs, expected):
    assert json2_body(method, args, kwargs) == expected


def test_unmapped_method_is_refused_before_any_request():
    with pytest.raises(OdooError) as exc:
        json2_body("unlink", [[1]], {})
    assert exc.value.code == "unsupported_method"


def test_version_from_public_route_and_saas_major(fake):
    fake.version = 18
    c = j2(fake)
    assert c.version()["server_version"] == "18.0" and c.server_major == 18
    assert major_version(["saas~18", 2]) == 18 and major_version([17, 0]) == 17 and major_version(None) == 0
    assert "Authorization" not in fake.last_json2[2]  # version_info is public: the key is not sent


def test_name_search_uses_domain_from_odoo_18(fake):
    fake.version = 18
    c = j2(fake)
    c.name_search("crm.stage", "qual", domain=[("fold", "=", False)])
    assert "domain" in fake.last_json2[1] and "args" not in fake.last_json2[1]


def test_uid_from_context_get_without_login(fake):
    assert j2(fake).uid == 2


def test_uid_from_login_when_context_has_none(fake):
    fake.context_uid = False
    c = j2(fake, username="admin")
    assert c.uid == 2 and c.user_context()["name"] == "Marc Demo"


def test_unknown_user_degrades_to_language_and_time_zone(fake):
    fake.context_uid = False
    c = j2(fake)
    ctx = c.user_context()
    assert ctx["tz"] == "Europe/Rome" and ctx["company_ids"] == []
    with pytest.raises(OdooError) as exc:
        _ = c.uid
    assert exc.value.code == "unknown_user" and "ODOO_USERNAME" in exc.value.hint


def test_bad_key_is_auth_failed_and_never_echoed(fake):
    c = OdooClient("https://odoo.test", "", "", "wrong-key", protocol="json2", json2_transport=fake.json2, areas=AREAS)
    with pytest.raises(OdooError) as exc:
        c.search_count("crm.lead", [])
    assert exc.value.code == "auth_failed" and "wrong-key" not in json.dumps(exc.value.to_dict())


@pytest.mark.parametrize(("status", "body", "code"), [
    (403, {"name": "odoo.exceptions.AccessError", "message": "no access"}, "access_denied"),
    (404, None, "json2_unavailable"),
    (404, {"name": "odoo.exceptions.MissingError", "message": "no method"}, "model_not_installed"),
    (422, {"name": "builtins.ValueError", "message": "Invalid field 'x' on model 'crm.lead'"}, "invalid_field"),
    (422, {"name": "odoo.exceptions.ValidationError", "message": "bad stage"}, "validation_error"),
    (503, None, "rate_limited"),
])
def test_http_errors_map_to_stable_codes(status, body, code):
    c = OdooClient("https://odoo.test", "", "", "k", protocol="json2", areas=AREAS,
                   json2_transport=lambda *a: (status, body))
    with pytest.raises(OdooError) as exc:
        c.search_count("crm.lead", [])
    assert exc.value.code == code


def test_debug_and_key_never_leave_the_error():
    body = {"name": "odoo.exceptions.AccessError", "message": "denied for k3y", "debug": "Traceback secret"}
    c = OdooClient("https://odoo.test", "", "", "k3y", protocol="json2", areas=AREAS,
                   json2_transport=lambda *a: (403, body))
    with pytest.raises(OdooError) as exc:
        c.search_count("crm.lead", [])
    text = json.dumps(exc.value.to_dict())
    assert "k3y" not in text and "Traceback" not in text


def test_area_gate_and_write_allow_list_hold_on_json2(fake):
    c = j2(fake, areas=("crm",))
    with pytest.raises(OdooError) as exc:
        c.search_read("res.partner", [], ["name"])
    assert exc.value.code == "area_not_enabled"
    with pytest.raises(OdooError) as exc:
        c.write_kw("crm.lead", "unlink", [[10]])
    assert exc.value.code == "write_not_allowed"
    assert [m for _s, _m, m, om in fake.calls if om not in (None, "context_get")] == []


def test_api_auto_resolution():
    both = {"ODOO_DB": "d", "ODOO_USERNAME": "u"}
    assert resolve_protocol(Settings(), both) == "jsonrpc"
    assert resolve_protocol(Settings(), {"ODOO_USERNAME": "u"}) == "json2"
    assert resolve_protocol(Settings(api="json2"), both) == "json2"


def test_json2_needs_only_url_and_key(monkeypatch):
    for n in ("ODOO_DB", "ODOO_USERNAME"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("ODOO_URL", "https://odoo.test")
    monkeypatch.setenv("ODOO_API_KEY", "test-key")
    assert settings_mod.is_configured() is True
    settings_mod.bind_context(FakeCtx({"api": "jsonrpc"}))
    assert settings_mod.is_configured() is False  # JSON-RPC forced without database and login


def test_bad_api_setting_is_a_config_error(monkeypatch):
    settings_mod.bind_context(FakeCtx({"api": "soap"}))
    assert settings_mod.load_settings().config_errors


def test_ping_reports_api_and_missing_user(monkeypatch):
    fake = FakeOdoo()
    fake.context_uid = False
    for n in ("ODOO_DB", "ODOO_USERNAME"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("ODOO_URL", "https://odoo.test")
    monkeypatch.setenv("ODOO_API_KEY", "test-key")
    monkeypatch.setattr(client_mod, "default_json2_transport", fake.json2)
    settings_mod.bind_context(FakeCtx({}))
    info = tools.ping(settings_mod.make_client(), settings_mod.load_settings())
    assert info["api"] == "json2" and info["uid"] is None and "ODOO_USERNAME" in info["user_note"]
    assert info["enabled_areas"] == [] and info["crm_installed"] is True
