#!/usr/bin/env python3
"""Exercise every tool against a real Odoo (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY in the env).

    uv run python dev/live_check.py            # prints one line per tool + the first failure in full
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load() -> object:
    spec = importlib.util.spec_from_file_location("alfred_odoo", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["alfred_odoo"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    pkg = load()
    tools = sys.modules["alfred_odoo.tools"]
    settings = sys.modules["alfred_odoo.settings"]
    print("ping:", json.dumps(tools.ping(settings.make_client()), ensure_ascii=False))
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
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
