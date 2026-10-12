alfred-odoo is installed (Odoo CRM tools, toolset `odoo`). It reads nothing until you enable data
areas, and writes nothing until you enable writes.

Next steps:
1. `hermes odoo test` — authenticates with the credentials you just entered and reports the Odoo
   version, the connected user, its companies, whether the CRM is reachable and whether writes are on.
2. Enable the data areas you have checked with the customer (default: none):
   `hermes config set plugins.entries.alfred-odoo.settings.enabled_areas crm,contacts`
   Areas: `crm` (leads, stages, teams, tags), `contacts`, `activities`, `chatter` (messages, notes,
   stage history). A tool whose area is off is not offered to the model.
3. Restart the gateway if it is running (`hermes gateway restart`) so the tools load there too.
4. Optional settings (Desktop → Plugins → Odoo CRM, or `hermes config set plugins.entries.alfred-odoo.settings.<key> <value>`):
   default_limit, timezone, default_team, advanced_tools, write_enabled, enabled_areas,
   revenue_range (min,max: implausible amounts kept out of totals), custom_fields (label=field, e.g. model=x_studio_modello_auto_1).

Writes: with `write_enabled: true` the assistant can log an internal note, schedule an activity or
move a deal to another stage. Each of these is shown to you first (a prompt in the CLI, buttons in
Telegram) and runs only after you approve it; "always" pre-approves only that identical change.

Everyone who can talk to this assistant sees — and, with writes enabled, changes — what the
configured Odoo user can: use a dedicated Odoo user with Sales / User rights only.
