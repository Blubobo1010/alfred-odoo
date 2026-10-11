# alfred-odoo

Read-only Odoo CRM tools for [Alfred](https://github.com/Blubobo1010/Alfred-Ai-Audit) / Hermes Agent:
leads and opportunities, pipeline summaries, customers, activities and chatter, over Odoo's
JSON-RPC API. Works with Odoo 16, 17 and 18 (Community or Enterprise, self-hosted, odoo.sh or
Odoo Online on a plan that allows the external API).

## Install

```bash
alfred plugins install Blubobo1010/alfred-odoo --enable
# or: hermes plugins install Blubobo1010/alfred-odoo --enable
```

The installer asks for four values and stores them in your Hermes `.env`:

| Variable | Value |
|---|---|
| `ODOO_URL` | Base URL, e.g. `https://mycompany.odoo.com` |
| `ODOO_DB` | Database name |
| `ODOO_USERNAME` | Login of the Odoo user the assistant acts as |
| `ODOO_API_KEY` | API key of that user (Odoo → avatar → My Profile → Account Security → New API Key) |

Then run `alfred odoo test` and restart the gateway (`alfred gateway restart`).

### Recommended Odoo setup

Create a dedicated Odoo user (e.g. `alfred-bot`) with **Sales / User: All Documents** and no
administration rights, and generate the API key from that user. Odoo's own access rules then
bound everything the assistant can read, including multi-company scoping. Everyone who can
message the assistant sees what that user sees.

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

Settings (`alfred plugins configure alfred-odoo` or the Desktop plugin page): `default_limit`,
`timezone`, `lang`, `company_ids`, `default_team`, `timeout_seconds`, `max_body_chars`,
`advanced_tools`.

## What this plugin does on your machine

- Makes HTTPS requests only to `ODOO_URL` (`/jsonrpc`); plain `http://` is accepted for
  localhost and private networks only.
- Calls only read methods (`search_read`, `read`, `search_count`, `read_group`, `fields_get`,
  `name_search`) on an allow-list of CRM, partner, user and mail models; writes are refused in
  code.
- Stores nothing but what Hermes stores for every plugin; no telemetry, no other network calls.
- Reads the credentials through Hermes' profile-scoped secret store.

## Development

```bash
uv run --group dev pytest -q tests   # unit tests with a fake JSON-RPC transport
uvx ruff check .
hermes plugins doctor . --ci      # real plugin loading with the network blocked
hermes plugins validate .         # manifest + security scan (must be "safe")
docker compose -f dev/docker-compose.yml up   # Odoo 17 demo database for manual testing
```

## License

MIT
