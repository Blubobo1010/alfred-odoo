"""The pre_tool_call hook: approve (with a per-call rule key) for write tools, nothing for reads, block when off."""

import pytest
from alfred_odoo import settings as settings_mod
from alfred_odoo.approvals import SUMMARY_MAX_CHARS, WRITE_TOOLS, approval_summary, pre_tool_call, rule_key
from conftest import FakeCtx


def test_write_tools_match_the_registered_names():
    import alfred_odoo

    assert set(WRITE_TOOLS) == {name for name, *_ in alfred_odoo._TOOLS if _[-1] is settings_mod.write_enabled}


@pytest.mark.parametrize("tool", ["odoo_search_leads", "odoo_get_lead", "terminal", "", None])
def test_read_tools_and_other_tools_pass_through(tool, fake):
    settings_mod.bind_context(FakeCtx({"write_enabled": True}))
    assert pre_tool_call(tool_name=tool, args={"lead_id": 10}, task_id="t", session_id="s") is None
    assert fake.calls == []


@pytest.mark.parametrize("tool", WRITE_TOOLS)
def test_write_tools_blocked_when_writes_disabled(tool):
    settings_mod.bind_context(FakeCtx({}))
    out = pre_tool_call(tool_name=tool, args={"lead_id": 10, "note": "x"})
    assert out["action"] == "block" and "write_enabled" in out["message"]


def test_write_tools_require_approval_with_per_call_rule_key(configured):
    settings_mod.bind_context(FakeCtx({"write_enabled": True}))
    out = pre_tool_call(tool_name="odoo_log_note", args={"lead_id": 10, "note": "Called the client"}, task_id="t")
    assert out["action"] == "approve"
    assert out["message"] == 'Odoo: log an internal note on lead #10: "Called the client"'
    assert out["rule_key"].startswith("alfred-odoo:odoo_log_note:") and len(out["rule_key"].split(":")[-1]) == 16
    assert configured.calls == []  # no network in the hook


def test_rule_key_is_stable_for_equal_args_and_distinct_otherwise():
    a = rule_key("odoo_log_note", {"lead_id": 10, "note": "x"})
    assert a == rule_key("odoo_log_note", {"note": "x", "lead_id": 10})
    assert a != rule_key("odoo_log_note", {"lead_id": 11, "note": "x"})
    assert a != rule_key("odoo_update_lead_stage", {"lead_id": 10, "note": "x"})


def test_summaries_are_readable_and_bounded():
    assert approval_summary("odoo_update_lead_stage", {"lead_id": 7, "stage": "Won"}) == 'Odoo: move lead #7 to stage "Won"'
    s = approval_summary("odoo_schedule_activity", {"lead_id": 7, "summary": "Demo", "activity_type": "Meeting",
                                                    "deadline": "2026-10-15", "assign_to": "Alice"})
    assert s == 'Odoo: schedule activity "Demo" on lead #7 (type Meeting, due 2026-10-15, assigned to Alice)'
    assert approval_summary("odoo_schedule_activity", {"lead_id": 7, "summary": "Demo"}).endswith("(due today, assigned to the connected user)")
    long = approval_summary("odoo_log_note", {"lead_id": 7, "note": "word " * 500})
    assert len(long) <= SUMMARY_MAX_CHARS and long.endswith('…"')
    assert "\n" not in approval_summary("odoo_log_note", {"lead_id": 7, "note": "a\nb"})


def test_hook_failure_blocks_instead_of_passing(monkeypatch):
    settings_mod.bind_context(FakeCtx({"write_enabled": True}))
    monkeypatch.setattr("alfred_odoo.approvals.approval_summary", lambda *a, **k: 1 / 0)
    out = pre_tool_call(tool_name="odoo_log_note", args={"lead_id": 10})
    assert out["action"] == "block" and "ZeroDivisionError" in out["message"]


def test_non_dict_args_still_approve():
    settings_mod.bind_context(FakeCtx({"write_enabled": True}))
    out = pre_tool_call(tool_name="odoo_log_note", args="garbage")
    assert out["action"] == "approve" and "lead #?" in out["message"]
