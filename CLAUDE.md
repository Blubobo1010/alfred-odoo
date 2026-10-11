# alfred-odoo — istruzioni per Claude

Plugin Hermes/Alfred **read-only** per Odoo CRM (16/17/18), stdlib only. La root del repo È il pacchetto
plugin (`plugin.yaml` + `__init__.py`); gli utenti lo installano con
`alfred plugins install Blubobo1010/alfred-odoo --enable`.

## Comandi
- `uv run --no-project --with pytest pytest -q tests` — unit test con transport JSON-RPC finto (`tests/conftest.py`).
- `uvx ruff check .`
- Contratti Hermes reali: dall'albero Alfred `python -m hermes_cli.main plugins doctor <repo> --ci` e `plugins validate <repo>`.
- Test live: `docker compose -f dev/docker-compose.yml up` (Odoo 17 demo, admin/admin come API key), poi
  `ODOO_URL=http://localhost:8069 ODOO_DB=demo ODOO_USERNAME=admin ODOO_API_KEY=admin uv run --no-project python dev/live_check.py`.

## Invarianti
- **Nessun `pyproject.toml`** nella root: Hermes lo legge come dichiarazione di dipendenze e chiede
  conferma all'install (lint config in `ruff.toml`, pytest config in `tests/pytest.ini`).
- Nessuna dipendenza runtime; import interni solo relativi (`from .client import …`): il loader lo
  monta come `hermes_plugins.alfred_odoo[__profile]`.
- `register()` non fa I/O e registra sempre tutti i tool di `provides_tools`; la disponibilità è decisa
  da `check_fn` (`settings.is_configured` / `advanced_enabled`).
- Segreti solo via `agent.secret_scope.get_secret` (fallback env solo fuori da Hermes); l'API key non
  deve mai comparire in output o errori (test `test_api_key_never_appears_in_any_tool_output`).
- Sola lettura applicata nel client: allow-list di metodi (`READ_METHODS`) e modelli (`ALLOWED_MODELS`).
- Testi email/chatter escono come `untrusted_text` + `content_notice`: il core non li avvolge per i plugin.
- Lo scanner d'installazione deve restare "safe": niente `curl … $ODOO_API_KEY` o segreti ≥20 char nei doc/test.

## Compatibilità versioni Odoo
Campi richiesti sempre intersecati con `fields_get` (`client.available_fields`); `won_status` esiste
solo da Odoo 18 (17.0 demo non lo ha): open/won/lost cadono su `stage_id.is_won` / `active`.
