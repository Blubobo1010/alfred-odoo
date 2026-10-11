alfred-odoo is installed (read-only Odoo CRM tools, toolset `odoo`).

Next steps:
1. `hermes odoo test` — authenticates with the credentials you just entered and reports the Odoo
   version, the connected user, its companies and whether the CRM is reachable.
2. Restart the gateway if it is running (`hermes gateway restart`) so the tools load there too.
3. Optional settings (Desktop → Plugins → Odoo CRM, or `hermes plugins configure alfred-odoo`):
   default_limit, timezone, default_team, advanced_tools.

Everyone who can talk to this assistant sees what the configured Odoo user can see: use a
dedicated Odoo user with Sales / User rights only.
