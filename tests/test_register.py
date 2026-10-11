"""register(ctx) contract: every manifest tool registered under toolset `odoo`, no I/O, gated by check_fn."""

from pathlib import Path

import alfred_odoo
import pytest
from alfred_odoo import settings as settings_mod

ROOT = Path(__file__).resolve().parents[1]


class FakeCtx:
    def __init__(self, config=None):
        self.tools = {}
        self.skills = {}
        self.cli = {}
        self.config = config or {}

    def register_tool(self, name, toolset, schema, handler, check_fn=None, requires_env=None, is_async=False,
                      description="", emoji="", override=False):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, "check_fn": check_fn,
                            "requires_env": requires_env}

    def register_skill(self, name, path):
        self.skills[name] = Path(path)

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def get_config(self, key, default=None):
        return self.config.get(key, default)


def manifest_tools() -> list[str]:
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("provides_tools:", 1)[1].split("config_schema:", 1)[0]
    return [line.strip()[2:] for line in block.splitlines() if line.strip().startswith("- ")]


def test_register_matches_manifest_without_network(fake):
    ctx = FakeCtx()
    alfred_odoo.register(ctx)
    assert set(ctx.tools) == set(manifest_tools())
    assert {t["toolset"] for t in ctx.tools.values()} == {"odoo"}
    for name, t in ctx.tools.items():
        assert t["schema"]["name"] == name
        assert t["requires_env"] == ["ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"]
        assert callable(t["handler"]) and callable(t["check_fn"])
    assert ctx.skills["odoo-crm"].is_file()
    assert "odoo" in ctx.cli
    assert fake.calls == []


def test_check_fn_false_without_env_true_with(monkeypatch):
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert settings_mod.is_configured() is False
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.setenv(name, "x")
    assert settings_mod.is_configured() is True


def test_advanced_tools_gated_by_setting(configured):
    ctx = FakeCtx(config={"advanced_tools": False})
    alfred_odoo.register(ctx)
    assert ctx.tools["odoo_query"]["check_fn"]() is False
    assert ctx.tools["odoo_search_leads"]["check_fn"]() is True
    ctx.config["advanced_tools"] = True
    assert ctx.tools["odoo_query"]["check_fn"]() is True


def test_settings_read_live_from_ctx(configured):
    ctx = FakeCtx(config={"default_limit": 5, "company_ids": ["1", 2], "timezone": "UTC"})
    alfred_odoo.register(ctx)
    s = settings_mod.load_settings()
    assert s.default_limit == 5 and s.company_ids == (1, 2) and s.timezone == "UTC"


@pytest.mark.parametrize("name", ["plugin.yaml", "after-install.md", "README.md", "LICENSE", "skills/odoo-crm/SKILL.md"])
def test_shipped_files_exist(name):
    assert (ROOT / name).is_file()
