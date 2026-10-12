# alfred-odoo — istruzioni per Claude

Plugin Hermes/Alfred per Odoo CRM (16/17/18), stdlib only: tool di lettura più tre scritture opt-in
(`odoo_log_note`, `odoo_schedule_activity`, `odoo_update_lead_stage`) approvate da un umano. La root del
repo È il pacchetto plugin (`plugin.yaml` + `__init__.py`); gli utenti lo installano con
`alfred plugins install Blubobo1010/alfred-odoo --enable`.

## Comandi
- `uv run --no-project --with pytest pytest -q tests` — unit test con transport JSON-RPC finto (`tests/conftest.py`).
- `uvx ruff check .`
- Contratti Hermes reali: dall'albero Alfred `python -m hermes_cli.main plugins doctor <repo> --ci` e `plugins validate <repo>`
  (deve essere "safe": un verdetto "caution" fa fallire la CI).
- Test live: `docker compose -f dev/docker-compose.yml up` (Odoo 17 demo, admin/admin come API key), poi
  `ODOO_URL=http://localhost:8069 ODOO_DB=demo ODOO_USERNAME=admin ODOO_API_KEY=admin uv run --no-project python dev/live_check.py`
  (`--writes` esercita anche le tre scritture su un lead demo e lo riporta allo stage iniziale).

## Invarianti
- **Nessun `pyproject.toml`** nella root: Hermes lo legge come dichiarazione di dipendenze e chiede
  conferma all'install (lint config in `ruff.toml`, pytest config in `tests/pytest.ini`).
- Nessuna dipendenza runtime; import interni solo relativi (`from .client import …`): il loader lo
  monta come `hermes_plugins.alfred_odoo[__profile]`.
- `register()` non fa I/O e registra sempre tutti i tool di `provides_tools` e l'hook di `provides_hooks`;
  la disponibilità è decisa da `check_fn` (`settings.is_configured` / `advanced_enabled` / `write_enabled`).
- Segreti solo via `agent.secret_scope.get_secret` (fallback env solo fuori da Hermes); l'API key non
  deve mai comparire in output o errori (test `test_api_key_never_appears_in_any_tool_output`).
- **Aree dati default-deny** (`client.AREA_MODELS`: `crm`, `contacts`, `activities`, `chatter`; setting
  `enabled_areas`, vuoto = nulla). `execute_kw` e `write_kw` rifiutano i record di un'area spenta
  (`area_not_enabled`), tranne `fields_get` (solo schema); i modelli `META_MODELS` (utenti, società, valute,
  `ir.model`) sono sempre leggibili. Il `check_fn` di ogni tool (`settings.tool_check`) richiede le sue aree;
  i tool che combinano più aree (`odoo_get_lead`, `odoo_timeline`, vocabolario) saltano le parti spente e
  le elencano in `areas_not_enabled`. Ripresa dal gate «Bivio B» di JarvisXCeo.
- Regole dati per cliente, solo da setting (mai nomi di campi `x_studio_*` nel codice): `revenue_range`
  (importi fuori intervallo marcati `suspect`, esclusi dalle somme del riepilogo che in quel caso aggrega
  lato client fino a 5000 righe) e `custom_fields` (`label=campo`, solo scalari/many2one, mostrati sui
  lead e validi come `group_by`). Un setting malformato non nasconde i tool: finisce in
  `Settings.config_errors` e ogni chiamata torna `bad_config`.
- Letture solo via `execute_kw` (allow-list `READ_METHODS` / `ALLOWED_MODELS`, resta read-only). Scritture
  solo via `write_kw` con allow-list `WRITE_OPS` = `crm.lead.message_post`, `mail.activity.create`,
  `crm.lead.write` (quest'ultima limitata a `LEAD_WRITABLE_FIELDS` = `stage_id`). Aggiungere una scrittura
  = aggiungere la coppia a `WRITE_OPS` + un ramo nel `FakeOdoo` dei test + un riassunto in `approvals.py`.
- Approvazione: `approvals.pre_tool_call` (hook Hermes `pre_tool_call`) ritorna `{"action":"approve",
  "message", "rule_key"}` per ogni write tool; il `message` è l'unica cosa che l'umano vede (CLI o card
  Telegram), il `rule_key` è un hash di tool+argomenti così "Always" copre solo la chiamata identica.
  Il hook non fa rete, ha 30 s di timeout e su qualsiasi errore ritorna `block`. I write tool ricontrollano
  `write_enabled` anche nel handler.
- Testi email/chatter escono come `untrusted_text` + `content_notice`: il core non li avvolge per i plugin.
- Lo scanner d'installazione deve restare "safe": nei documenti e nei test non scrivere comandi shell che
  leggono la variabile della API key (regola `env_exfil_curl`) né stringhe simili a segreti (≥20 caratteri).

## Impostazioni
Non esiste `plugins configure`: i setting del manifest vivono in `plugins.entries.alfred-odoo.settings.<key>`
(`alfred config set …` o pagina Plugins del Desktop); `ctx.get_config` non applica i default del manifest.

## Compatibilità versioni Odoo
Campi richiesti sempre intersecati con `fields_get` (`client.available_fields`); `won_status` esiste
solo da Odoo 18 (17.0 demo non lo ha): open/won/lost cadono su `stage_id.is_won` / `active`.
`mail.activity.create` richiede `res_model_id` (id di `ir.model`, letto e cacheato), non `res_model`.
