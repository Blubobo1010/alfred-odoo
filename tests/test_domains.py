from datetime import date

import pytest
from alfred_odoo import domains
from alfred_odoo.client import AREAS, OdooClient, OdooError
from conftest import FakeOdoo


@pytest.mark.parametrize(
    "status,has_won,expected_domain,expected_ctx",
    [
        ("open", True, [("won_status", "=", "pending")], {}),
        ("won", True, [("won_status", "=", "won")], {}),
        ("lost", True, [("won_status", "=", "lost")], {"active_test": False}),
        ("open", False, [("active", "=", True), ("stage_id.is_won", "=", False)], {}),
        ("won", False, [("stage_id.is_won", "=", True)], {}),
        ("lost", False, [("active", "=", False), ("probability", "=", 0)], {"active_test": False}),
        ("all", True, [], {"active_test": False}),
    ],
)
def test_status_domain_per_version(status, has_won, expected_domain, expected_ctx):
    assert domains.status_domain(status, has_won_status=has_won) == (expected_domain, expected_ctx)


def test_status_domain_rejects_unknown():
    with pytest.raises(OdooError):
        domains.status_domain("maybe", has_won_status=True)


def test_text_domain_is_polish_or():
    assert domains.text_domain("acme", ["name", "email_from"]) == ["|", ("name", "ilike", "acme"), ("email_from", "ilike", "acme")]
    assert domains.text_domain("", ["name"]) == []


def test_date_range_and_validation():
    assert domains.date_range("date_deadline", "2026-01-01", None) == [("date_deadline", ">=", "2026-01-01")]
    assert domains.date_range("create_date", "2026-01-01", "2026-01-31", is_datetime=True) == [
        ("create_date", ">=", "2026-01-01 00:00:00"), ("create_date", "<=", "2026-01-31 23:59:59")]
    with pytest.raises(OdooError):
        domains.parse_date("31/01/2026", "from")


def test_lead_domain_resolves_names_17(client):
    domain, ctx, resolved = domains.lead_domain(client, {"stage": "qualif", "salesperson": "marc", "team": "eur", "text": "acme"})
    assert ("won_status", "=", "pending") in domain
    assert ("type", "=", "opportunity") in domain
    assert ("stage_id", "in", [2]) in domain
    assert ("user_id", "in", [2]) in domain
    assert ("team_id", "in", [1]) in domain
    assert ("name", "ilike", "acme") in domain
    assert resolved == {"stage": "Qualified", "salesperson": "Marc Demo", "team": "Europe"}
    assert ctx == {}


def test_lead_domain_16_has_no_won_status():
    client16 = OdooClient("https://odoo.test", "demo", "admin", "test-key", areas=AREAS, transport=FakeOdoo(version=16))
    domain, _, _ = domains.lead_domain(client16, {"status": "open"})
    assert ("stage_id.is_won", "=", False) in domain
    assert not any(t[0] == "won_status" for t in domain if isinstance(t, tuple))


def test_lead_domain_me_and_no_match(client):
    _, _, resolved = domains.lead_domain(client, {"salesperson": "me"})
    assert resolved["salesperson"] == "Marc Demo"
    with pytest.raises(OdooError) as exc:
        domains.lead_domain(client, {"stage": "nonexistent"})
    assert exc.value.code == "no_match"
    assert "Qualified" in exc.value.hint


def test_lead_domain_default_team_applies_only_without_team(client):
    domain, _, _ = domains.lead_domain(client, {}, default_team="Europe")
    assert ("team_id", "in", [1]) in domain


def test_activity_window():
    today = date(2026, 10, 14)  # a Wednesday
    assert domains.activity_window("overdue", today)[0] == [("date_deadline", "<", "2026-10-14")]
    assert domains.activity_window("today", today)[0] == [("date_deadline", "=", "2026-10-14")]
    assert domains.activity_window("this_week", today)[0] == [("date_deadline", ">=", "2026-10-12"), ("date_deadline", "<=", "2026-10-18")]
    assert domains.activity_window(None, today)[1] == "overdue_and_today"
    assert domains.activity_state("2026-10-01", today) == "overdue"
    assert domains.activity_state("2026-10-14", today) == "today"
    assert domains.activity_state("2026-10-20", today) == "planned"


@pytest.mark.parametrize(
    "domain,ok",
    [
        ([["stage_id.name", "ilike", "won"]], True),
        (["|", ["probability", ">", 50], ["expected_revenue", ">=", 1000]], True),
        ([["name", "=", ["a", "b"]]], True),
        ([["name", "=", {"x": 1}]], False),
        ([["name; drop", "=", "x"]], False),
        ([["name", "regexp", "x"]], False),
        ([["a.b.c.d.e", "=", 1]], False),
        ("name = x", False),
        ([["name", "=", "x"]] * 31, False),
    ],
)
def test_validate_domain(domain, ok):
    if ok:
        assert domains.validate_domain(domain)
    else:
        with pytest.raises(OdooError):
            domains.validate_domain(domain)
