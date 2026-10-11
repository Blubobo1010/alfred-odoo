import pytest
from alfred_odoo.client import WRITE_OPS, OdooClient, OdooError, ReadOnlyViolation, map_error, normalize_url
from conftest import FakeOdoo


@pytest.mark.parametrize("method", ["write", "create", "unlink", "message_post", "action_set_won", "copy", "execute"])
def test_write_methods_refused_without_io(client, fake, method):
    with pytest.raises(ReadOnlyViolation):
        client.execute_kw("crm.lead", method, [[1], {"name": "x"}])
    assert fake.calls == []


@pytest.mark.parametrize("model,method", [
    ("crm.lead", "unlink"), ("crm.lead", "create"), ("crm.lead", "action_set_won"), ("res.partner", "write"),
    ("mail.activity", "unlink"), ("mail.message", "create"), ("sale.order", "write"),
])
def test_write_kw_refuses_everything_outside_the_allow_list(client, fake, model, method):
    with pytest.raises(OdooError) as exc:
        client.write_kw(model, method, [[1], {"stage_id": 1}])
    assert exc.value.code == "write_not_allowed"
    assert fake.calls == [] and fake.writes == []


@pytest.mark.parametrize("values", [{"name": "x"}, {"stage_id": 1, "user_id": 2}, {}])
def test_write_kw_lead_write_restricted_to_stage(client, fake, values):
    with pytest.raises(OdooError) as exc:
        client.write_kw("crm.lead", "write", [[10], values])
    assert exc.value.code == "field_not_writable"
    assert fake.writes == []


def test_write_kw_allowed_operations_reach_odoo(client, fake):
    assert client.write_kw("crm.lead", "message_post", [[10]], {"body": "<p>x</p>"}) == 123
    assert client.write_kw("mail.activity", "create", [{"res_id": 10}]) == 77
    assert client.write_kw("crm.lead", "write", [[10], {"stage_id": 3}]) is True
    assert [(m, op) for m, op, _, _ in fake.writes] == [("crm.lead", "message_post"), ("mail.activity", "create"), ("crm.lead", "write")]
    assert fake.writes[0][3] == {"body": "<p>x</p>"}
    assert fake.leads[0]["stage_id"] == [3, "Proposition"]


def test_write_kw_retries_once_after_expired_session(client, fake):
    client.uid  # noqa: B018 - prime the session
    fake.fail_next = {"data": {"name": "odoo.exceptions.AccessDenied", "message": "Session expired"}}
    assert client.write_kw("crm.lead", "message_post", [[10]], {"body": "x"}) == 123
    assert sum(1 for c in fake.calls if c[1] == "authenticate") == 2
    assert len(fake.writes) == 1


def test_write_ops_is_the_whole_write_surface():
    assert WRITE_OPS == {("crm.lead", "message_post"), ("crm.lead", "write"), ("mail.activity", "create")}


def test_non_allowlisted_model_refused(client, fake):
    with pytest.raises(OdooError) as exc:
        client.search_read("sale.order", [], ["name"])
    assert exc.value.code == "model_not_allowed"
    assert fake.calls == []


def test_authenticate_false_maps_to_auth_failed(fake):
    bad = OdooClient("https://odoo.test", "demo", "admin", "wrong", transport=fake)
    with pytest.raises(OdooError) as exc:
        bad.search_count("crm.lead", [])
    assert exc.value.code == "auth_failed"


def test_uid_cached_per_credentials(fake):
    a = OdooClient("https://odoo.test", "demo", "admin", "test-key", transport=fake)
    b = OdooClient("https://odoo.test", "demo", "admin", "test-key", transport=fake)
    assert a.uid == 2 and b.uid == 2
    assert sum(1 for c in fake.calls if c[1] == "authenticate") == 1


def test_retry_once_after_access_denied(client, fake):
    client.uid  # noqa: B018 - prime the session
    fake.fail_next = {"data": {"name": "odoo.exceptions.AccessDenied", "message": "Session expired"}}
    assert client.search_count("crm.lead", []) == 2
    assert sum(1 for c in fake.calls if c[1] == "authenticate") == 2


def test_fields_get_cached_and_available_fields(client, fake):
    client.available_fields("crm.lead", ["name", "nope"])
    client.available_fields("crm.lead", ["won_status"])
    assert sum(1 for c in fake.calls if c[3] == "fields_get") == 1
    assert client.available_fields("crm.lead", ["name", "nope", "won_status"]) == ["name", "won_status"]


@pytest.mark.parametrize(
    "payload,code",
    [
        ({"error": {"data": {"name": "odoo.exceptions.AccessError", "message": "no"}}}, "access_denied"),
        ({"error": {"data": {"name": "KeyError", "message": "Object crm.lead doesn't exist"}}}, "model_not_installed"),
        ({"error": {"data": {"name": "ValueError", "message": "Invalid field 'foo' on model"}}}, "invalid_field"),
        ({"error": {"data": {"name": "psycopg2.OperationalError", "message": 'database "x" does not exist'}}}, "bad_database"),
        ({"error": {"data": {"name": "odoo.exceptions.ValidationError", "message": "Invalid stage"}}}, "validation_error"),
        ({"error": {"data": {"name": "odoo.exceptions.UserError", "message": "Cannot move a lost lead"}}}, "validation_error"),
        ({"error": {"data": {"name": "Other", "message": "boom"}}}, "odoo_error"),
    ],
)
def test_error_mapping(payload, code):
    assert map_error(payload).code == code


def test_api_key_never_leaks_into_errors():
    secret = "sk-very-secret-value"
    payload = {"error": {"data": {"name": "X", "message": f"bad key {secret} rejected", "debug": secret}}}
    err = map_error(payload, secret)
    assert secret not in err.message and secret not in (err.hint or "")


def test_url_normalization_and_scheme_policy():
    assert normalize_url("https://x.odoo.com/web/") == "https://x.odoo.com"
    assert normalize_url("https://x.odoo.com/odoo") == "https://x.odoo.com"
    assert normalize_url("http://localhost:8069") == "http://localhost:8069"
    assert normalize_url("http://10.0.0.5:8069/jsonrpc") == "http://10.0.0.5:8069"
    with pytest.raises(OdooError):
        normalize_url("http://erp.example.com")
    with pytest.raises(OdooError):
        normalize_url("erp.example.com")


def test_version_major_and_user_context(client):
    assert client.server_major == 17
    assert client.user_context()["tz"] == "Europe/Rome"


def test_missing_credentials_rejected():
    with pytest.raises(OdooError) as exc:
        OdooClient("https://odoo.test", "demo", "", "k", transport=FakeOdoo())
    assert exc.value.code == "not_configured"
