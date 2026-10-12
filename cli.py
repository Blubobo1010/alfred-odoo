"""`alfred odoo test | fields <model> | whoami` — connection checks for the operator."""

from __future__ import annotations

import argparse
import sys

from .client import ALLOWED_MODELS, OdooError
from .settings import load_settings, make_client
from .tools import as_json, ping


def register_cli(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="odoo_command")
    sub.add_parser("test", help="Authenticate and report version, user, companies, CRM availability and write status")
    sub.add_parser("whoami", help="Show the connected Odoo user")
    p = sub.add_parser("fields", help="List the fields of an allow-listed model")
    p.add_argument("model", help="e.g. crm.lead")
    p.add_argument("--filter", default="", help="Only fields whose name or label contains this")


def odoo_command(args: argparse.Namespace) -> int:
    command = getattr(args, "odoo_command", None) or "test"
    try:
        settings = load_settings()
        client = make_client(settings)
        if command == "test":
            info = ping(client, settings)
            print(as_json(info))
            if not info.get("crm_installed"):
                print("CRM is not reachable with these credentials.", file=sys.stderr)
                return 1
            print("Odoo connection OK.")
            return 0
        if command == "whoami":
            user = client.user_context()
            print(f"{user.get('name')} (uid {client.uid}) on {client.url}" + (f" / {client.db}" if client.db else "")
                  + f" via {client.protocol}")
            return 0
        if command == "fields":
            if args.model not in ALLOWED_MODELS:
                print(f"{args.model} is not allow-listed; allowed: {', '.join(sorted(ALLOWED_MODELS))}", file=sys.stderr)
                return 2
            needle = args.filter.lower()
            for name, meta in sorted(client.fields_get(args.model).items()):
                if needle and needle not in name.lower() and needle not in str(meta.get("string", "")).lower():
                    continue
                rel = f" -> {meta['relation']}" if meta.get("relation") else ""
                print(f"{name:40} {meta.get('type', ''):12} {meta.get('string', '')}{rel}")
            return 0
    except OdooError as exc:
        print(f"error [{exc.code}]: {exc.message}" + (f"\n  hint: {exc.hint}" if exc.hint else ""), file=sys.stderr)
        return 1
    print(f"unknown odoo command {command!r}", file=sys.stderr)
    return 2
