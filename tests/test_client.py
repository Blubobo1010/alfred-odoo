import pytest
from alfred_odoo.client import OdooClient, OdooError, ReadOnlyViolation, map_error, normalize_url
from conftest import FakeOdoo


@pytest.mark.parametrize("method", ["write", "create", "unlink", "message_post", "action_set_won", "copy", "execute"])
def test_write_methods_refused_without_io(client, fake, method):
    with pytest.raises(ReadOnlyViolation):
        client.execute_kw("crm.lead", method, [[1], {"name": "x"}])
    assert fake.calls == []


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
