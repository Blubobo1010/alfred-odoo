"""register(ctx) contract: every manifest tool registered under toolset `odoo`, no I/O, gated by check_fn."""

from pathlib import Path

import alfred_odoo
import pytest
from alfred_odoo import settings as settings_mod
from alfred_odoo.approvals import WRITE_TOOLS, pre_tool_call
from conftest import ALL_AREAS, FakeCtx

ROOT = Path(__file__).resolve().parents[1]


def manifest_tools() -> list[str]:
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("provides_tools:", 1)[1].split("provides_hooks:", 1)[0]
    return [line.strip()[2:] for line in block.splitlines() if line.strip().startswith("- ")]


def manifest_hooks() -> list[str]:
    text = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    block = text.split("provides_hooks:", 1)[1].split("config_schema:", 1)[0]
    return [line.strip()[2:] for line in block.splitlines() if line.strip().startswith("- ")]


def test_register_matches_manifest_without_network(fake):
    ctx = FakeCtx()
    alfred_odoo.register(ctx)
    assert set(ctx.tools) == set(manifest_tools())
    assert {t["toolset"] for t in ctx.tools.values()} == {"odoo"}
    for name, t in ctx.tools.items():
        assert t["schema"]["name"] == name
        assert t["requires_env"] == ["ODOO_URL", "ODOO_API_KEY"]
        assert callable(t["handler"]) and callable(t["check_fn"])
    assert ctx.skills["odoo-crm"].is_file()
    assert "odoo" in ctx.cli
    assert ctx.hooks == [("pre_tool_call", pre_tool_call)]
    assert [h for h, _ in ctx.hooks] == manifest_hooks()
    assert fake.calls == []


def test_check_fn_false_without_env_true_with(monkeypatch):
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert settings_mod.is_configured() is False
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.setenv(name, "x")
    assert settings_mod.is_configured() is True


def test_advanced_tools_gated_by_setting(configured):
    ctx = FakeCtx(config={"advanced_tools": False, "enabled_areas": ALL_AREAS})
    alfred_odoo.register(ctx)
    assert ctx.tools["odoo_query"]["check_fn"]() is False
    assert ctx.tools["odoo_search_leads"]["check_fn"]() is True
    ctx.config["advanced_tools"] = True
    assert ctx.tools["odoo_query"]["check_fn"]() is True


def test_write_tools_gated_by_setting(configured):
    ctx = FakeCtx({"enabled_areas": ALL_AREAS})
    alfred_odoo.register(ctx)
    for name in WRITE_TOOLS:
        assert ctx.tools[name]["check_fn"]() is False
    ctx.config["write_enabled"] = True
    for name in WRITE_TOOLS:
        assert ctx.tools[name]["check_fn"]() is True
    assert ctx.tools["odoo_query"]["check_fn"]() is False  # independent of advanced_tools


def test_write_tools_hidden_without_credentials(monkeypatch):
    for name in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    ctx = FakeCtx({"write_enabled": True})
    alfred_odoo.register(ctx)
    assert all(ctx.tools[name]["check_fn"]() is False for name in WRITE_TOOLS)


def test_settings_read_live_from_ctx(configured):
    ctx = FakeCtx(config={"default_limit": 5, "company_ids": ["1", 2], "timezone": "UTC"})
    alfred_odoo.register(ctx)
    s = settings_mod.load_settings()
    assert s.default_limit == 5 and s.company_ids == (1, 2) and s.timezone == "UTC"


@pytest.mark.parametrize("name", ["plugin.yaml", "after-install.md", "README.md", "LICENSE", "skills/odoo-crm/SKILL.md"])
def test_shipped_files_exist(name):
    assert (ROOT / name).is_file()
