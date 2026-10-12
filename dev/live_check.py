#!/usr/bin/env python3
"""Exercise every tool against a real Odoo (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY in the env).

    uv run python dev/live_check.py            # prints one line per tool + the first failure in full
    uv run python dev/live_check.py --writes   # also log a note, schedule an activity and move the
                                               # first open lead to another stage (then move it back)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# Data areas the check enables (default: all). LIVE_AREAS=crm,contacts checks a restricted setup.
AREAS = os.environ.get("LIVE_AREAS", "crm,contacts,activities,chatter")
# Optional settings: LIVE_API=json2|jsonrpc, data rules e.g. LIVE_REVENUE_RANGE=100000,2000000 LIVE_CUSTOM_FIELDS=model=x_studio_modello_auto_1
RULES = {k: os.environ[v] for k, v in (("revenue_range", "LIVE_REVENUE_RANGE"), ("custom_fields", "LIVE_CUSTOM_FIELDS"),
                                      ("api", "LIVE_API")) if os.environ.get(v)}


class _Ctx:
    """Stands in for the Hermes PluginContext: the areas above, writes on only when asked."""

    def __init__(self, writes: bool = False):
        self.values = {"enabled_areas": AREAS, "write_enabled": writes, **RULES}

    def get_config(self, key, default=None):
        return self.values.get(key, default)


def check_writes(tools, settings, lead_id: int) -> int:
    """Run the three write tools on ``lead_id`` and restore its stage; returns the failure count."""
    settings.bind_context(_Ctx(writes=True))
    failures = 0
    before = json.loads(tools.odoo_get_lead({"lead_id": lead_id, "include_timeline": False}))["lead"]
    vocab = json.loads(tools.odoo_crm_vocabulary({"include": ["stages"]}))
    other = next((s["name"] for s in vocab.get("stages", []) if s["name"] != before["stage"] and not s["is_won"]), None)
    calls = [
        ("odoo_log_note", {"lead_id": lead_id, "note": "alfred-odoo live_check: note <test> & done\nsecond line"}),
        ("odoo_schedule_activity", {"lead_id": lead_id, "summary": "alfred-odoo live_check", "deadline": "today", "assign_to": "me"}),
        ("odoo_update_lead_stage", {"lead_id": lead_id, "stage": other}),
        ("odoo_update_lead_stage", {"lead_id": lead_id, "stage": other}),  # no-op: changed=false
        ("odoo_update_lead_stage", {"lead_id": lead_id, "stage": before["stage"]}),
    ]
    for name, args in calls:
        if None in args.values():
            print(f"SKIP {name} (no other open stage)")
            continue
        out = json.loads(getattr(tools, name)(args))
        ok = out.get("ok")
        failures += 0 if ok else 1
        print(f"{'OK ' if ok else 'ERR'} {name} {json.dumps(args, ensure_ascii=False)[:120]} -> {json.dumps(out, ensure_ascii=False)[:300]}")
    timeline = json.loads(tools.odoo_timeline({"lead_id": lead_id, "kinds": ["notes", "activities"], "limit": 5}))
    print("timeline after writes:", json.dumps(timeline.get("entries", [])[:3], ensure_ascii=False)[:600])
    return failures


def load() -> object:
    spec = importlib.util.spec_from_file_location("alfred_odoo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["alfred_odoo"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    load()
    tools = sys.modules["alfred_odoo.tools"]
    settings = sys.modules["alfred_odoo.settings"]
    settings.bind_context(_Ctx())
    print("ping:", json.dumps(tools.ping(settings.make_client(), settings.load_settings()), ensure_ascii=False))
    calls = [
        ("odoo_crm_vocabulary", {}),
        ("odoo_search_leads", {"limit": 3}),
        ("odoo_search_leads", {"status": "won", "limit": 2}),
        ("odoo_search_leads", {"status": "lost", "limit": 2, "sort": "recent"}),
        ("odoo_search_leads", {"salesperson": "me", "text": "a", "limit": 2}),
        ("odoo_pipeline_summary", {}),
        ("odoo_pipeline_summary", {"group_by": "salesperson"}),
        ("odoo_pipeline_summary", {"group_by": "deadline_month"}),
        ("odoo_search_partners", {"text": "a", "limit": 3}),
        ("odoo_list_activities", {"assigned_to": "anyone", "due": "all", "limit": 3}),
        ("odoo_describe_model", {"model": "crm.lead", "filter": "stage"}),
        ("odoo_query", {"model": "crm.stage", "fields": ["name", "sequence", "is_won"], "domain": [], "limit": 10}),
    ]
    failures = 0
    first_lead = None
    first_partner = None
    for name, args in calls:
        out = json.loads(getattr(tools, name)(args))
        ok = out.get("ok")
        failures += 0 if ok else 1
        summary = out.get("error") if not ok else {k: out[k] for k in ("total", "count", "has_more") if k in out}
        print(f"{'OK ' if ok else 'ERR'} {name} {json.dumps(args)} -> {json.dumps(summary, ensure_ascii=False)[:200]}")
        if ok and name == "odoo_search_leads" and out.get("items") and first_lead is None:
            first_lead = out["items"][0]["id"]
        if ok and name == "odoo_search_partners" and out.get("items") and first_partner is None:
            first_partner = out["items"][0]["id"]
    for name, args in (
        ("odoo_get_lead", {"lead_id": first_lead}),
        ("odoo_timeline", {"lead_id": first_lead, "kinds": ["messages", "notes", "activities", "stage_changes"]}),
        ("odoo_timeline", {"partner_id": first_partner}),
    ):
        if None in args.values():
            print(f"SKIP {name} (no record)")
            continue
        out = json.loads(getattr(tools, name)(args))
        ok = out.get("ok")
        failures += 0 if ok else 1
        print(f"{'OK ' if ok else 'ERR'} {name} {json.dumps(args)} -> {json.dumps(out, ensure_ascii=False)[:300]}")
    if "--writes" in sys.argv:
        if first_lead is None:
            print("SKIP writes (no lead)")
        else:
            failures += check_writes(tools, settings, first_lead)
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
