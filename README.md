# alfred-odoo

Odoo CRM tools for [Alfred](https://github.com/Blubobo1010/Alfred-Ai-Audit) / Hermes Agent:
leads and opportunities, pipeline summaries, customers, activities and chatter, over Odoo's
JSON-2 API (Odoo 18 Online, 19+) or the classic JSON-RPC API (Odoo 16-18), plus three opt-in writes
(internal note, scheduled activity, stage change) that a human approves one by one. Community or
Enterprise, self-hosted, odoo.sh or Odoo Online on a plan that allows the external API.

## Install

```bash
alfred plugins install Blubobo1010/alfred-odoo --enable
# or: alfred plugins install Blubobo1010/alfred-odoo --enable
```

The installer asks for these values and stores them in your Hermes `.env`:

| Variable | JSON-2 | JSON-RPC | Value |
|---|---|---|---|
| `ODOO_URL` | required | required | Base URL, e.g. `https://mycompany.odoo.com` |
| `ODOO_API_KEY` | required | required | API key of the user the assistant acts as (Odoo → avatar → My Profile → Account Security → New API Key) |
| `ODOO_DB` | multi-database servers only | required | Database name |
| `ODOO_USERNAME` | recommended | required | Login of that user; with JSON-2 it lets "me" filters find the user |

The plugin setting `api` picks the wire: `json2` (`POST /json/2/<model>/<method>`, key as bearer),
`jsonrpc` (`/jsonrpc`, database + login + key), or `auto` (default: JSON-RPC when `ODOO_DB` and
`ODOO_USERNAME` are both set, JSON-2 otherwise). `alfred odoo test` reports which one is in use; on
an Odoo without JSON-2 the error `json2_unavailable` says to switch to `jsonrpc`.
The installer prompts only for the two required values: for JSON-RPC (or the JSON-2 login) add
`ODOO_DB=…` / `ODOO_USERNAME=…` to the Hermes `.env` (`~/.hermes/.env`, or the profile's).

Then enable the data areas (next section), run `alfred odoo test` and restart the gateway
(`alfred gateway restart`).

### Data areas (default-deny)

Out of the box the assistant reads **nothing** from Odoo. Every model belongs to a data area and
an area is used only once the operator enables it, after checking with the customer that its data
is clean enough to answer from (a well-formatted number that is wrong is worse than no number):

| Area | Models | Tools |
|---|---|---|
| `crm` | `crm.lead`, `crm.stage`, `crm.team`, `crm.tag`, `crm.lost.reason` | vocabulary, search/get lead, pipeline summary |
| `contacts` | `res.partner` | `odoo_search_partners` |
| `activities` | `mail.activity`, `mail.activity.type` | `odoo_list_activities`, activities in timelines |
| `chatter` | `mail.message`, `mail.tracking.value` | emails, notes and stage history in timelines |

```bash
alfred config set plugins.entries.alfred-odoo.settings.enabled_areas crm,contacts
alfred gateway restart
```

A tool whose area is off is not offered to the model; if a call still reaches a disabled area the
client refuses it (`area_not_enabled`) before anything is sent to Odoo. Users, companies,
currencies and model ids are always readable (session metadata), and `alfred odoo fields <model>`
works on every model so you can inspect an area before enabling it. `alfred odoo test` lists the
enabled and disabled areas.

### Recommended Odoo setup

Create a dedicated Odoo user (e.g. `alfred-bot`) with **Sales / User: All Documents** and no
administration rights, and generate the API key from that user. Odoo's own access rules then
bound everything the assistant can read or change, including multi-company scoping. Everyone who
can message the assistant sees what that user sees (and, with writes enabled, writes as that user:
notes and activities appear under its name).

## Tools (toolset `odoo`)

| Tool | Use |
|---|---|
| `odoo_crm_vocabulary` | stages, teams, salespeople, tags, lost reasons, activity types |
| `odoo_search_leads` | lists and counts of leads/opportunities with loose name filters |
| `odoo_get_lead` | one deal in full, with its latest timeline |
| `odoo_pipeline_summary` | count / expected / weighted revenue by stage, person, team, month, lost reason |
| `odoo_search_partners` | customers and contacts |
| `odoo_timeline` | emails, notes, activities and stage changes of a deal or customer |
| `odoo_list_activities` | overdue / today / this week activities |
| `odoo_describe_model`, `odoo_query` | generic read-only access (off unless `advanced_tools` is enabled) |
| `odoo_log_note` | post an internal note in a deal's chatter (off unless `write_enabled`) |
| `odoo_schedule_activity` | schedule a call / meeting / to-do on a deal with due date and assignee (off unless `write_enabled`) |
| `odoo_update_lead_stage` | move a deal to another pipeline stage, including a won stage (off unless `write_enabled`) |

Settings (Desktop → Plugins → Odoo CRM, or `alfred config set plugins.entries.alfred-odoo.settings.<key> <value>`): `default_limit`,
`timezone`, `lang`, `company_ids`, `default_team`, `timeout_seconds`, `max_body_chars`,
`advanced_tools`, `write_enabled`, `enabled_areas`, `revenue_range`, `custom_fields`.

- `revenue_range` (`min,max`, max 0 = no upper bound): expected revenues outside it are flagged
  `suspect` on leads, counted but left out of `odoo_pipeline_summary` sums, and listed under
  `suspect_amounts` (typos such as 880 for 880,000). With a range set the summary aggregates up to
  5,000 matching leads client-side and says `truncated` beyond that.
- `custom_fields` (`label=field,...`): company-specific `crm.lead` fields (plain or many2one) shown
  on leads under `custom_fields` and usable as `group_by` labels, e.g.
  `alfred config set plugins.entries.alfred-odoo.settings.custom_fields model=x_studio_modello_auto_1`.

## Writes (opt-in)

Writing is off by default: the three write tools are not even offered to the model until
`write_enabled` is `true` (Desktop → Plugins → Odoo CRM, or
`alfred config set plugins.entries.alfred-odoo.settings.write_enabled true`, then restart the gateway). Once enabled, **every write is confirmed by a human before it runs**:
Hermes stops the tool call and shows a one-line summary of the change ("Odoo: move lead #42 to
stage "Won"") in the CLI prompt or as a Telegram card with buttons. The choices are the usual
Hermes ones:

| Choice | Effect |
|---|---|
| once | run this change |
| session | run it, and skip the prompt for this identical change until the gateway restarts |
| always | run it, and remember this identical change permanently (`command_allowlist` in config.yaml) |
| deny | the tool returns `BLOCKED` and the assistant reports it |

"Always" never pre-approves a whole tool: the remembered key is a hash of the tool and its exact
arguments, so a different note, activity or stage asks again. No answer within
`approvals.timeout` (default 300 s) counts as a denial; `--yolo` / `approvals.mode: off` skip the
prompt like they do for shell commands.

What a write can touch is fixed in code: `crm.lead.message_post` (note), `mail.activity.create`
(activity) and `crm.lead.write` limited to `stage_id`. Nothing else is writable, whatever the
model asks for.

## What this plugin does on your machine

- Makes HTTPS requests only to `ODOO_URL` (`/json/2/…` and the public `/web/webclient/version_info`,
  or `/jsonrpc`); plain `http://` is accepted for localhost and private networks only.
- Reads through read methods only (`search_read`, `read`, `search_count`, `read_group`,
  `fields_get`, `name_search`) on an allow-list of CRM, partner, user and mail models.
- Writes, when enabled, only through the three operations above, each gated by the human
  approval prompt; any other write is refused in code.
- Stores nothing but what Hermes stores for every plugin; no telemetry, no other network calls.
- Reads the credentials through Hermes' profile-scoped secret store.

## Development

```bash
uv run --no-project --with pytest pytest -q tests   # unit tests on fake JSON-2 and JSON-RPC transports
uvx ruff check .
alfred plugins doctor . --ci      # real plugin loading with the network blocked
alfred plugins validate .         # manifest + security scan (must be "safe")
docker compose -f dev/docker-compose.yml up   # Odoo 17 demo database for manual testing
uv run --no-project python dev/live_check.py --writes   # every tool against it, writes included
```

## License

MIT
