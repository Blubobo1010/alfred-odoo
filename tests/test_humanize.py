import json
from zoneinfo import ZoneInfo

from alfred_odoo import humanize as h


def test_m2o():
    assert h.m2o([3, "Qualified"]) == "Qualified"
    assert h.m2o(False) is None
    assert h.m2o_id([3, "Qualified"]) == 3
    assert h.m2o_id(False) is None


def test_html_to_text_strips_markup_quotes_and_styles():
    html = ("<style>.x{color:red}</style><p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul>"
            "<blockquote>quoted history</blockquote>&amp; done")
    assert h.html_to_text(html) == "Hello world\none\ntwo\n& done"


def test_html_to_text_cuts_outlook_reply_and_truncates():
    text = h.html_to_text("<p>Thanks</p>\n<p>On Mon, Jan 1, 2026 John wrote:</p><p>old stuff</p>")
    assert text == "Thanks"
    assert h.html_to_text("<p>" + "x" * 100 + "</p>", max_chars=20).endswith(h.TRUNCATED)
    assert len(h.html_to_text("<p>" + "x" * 100 + "</p>", max_chars=20)) == 20


def test_fmt_dt_converts_utc_to_tz():
    rome = ZoneInfo("Europe/Rome")
    assert h.fmt_dt("2026-10-01 09:30:00", rome) == "2026-10-01T11:30+02:00"
    assert h.fmt_dt("2026-10-01", rome) == "2026-10-01"
    assert h.fmt_dt(False, rome) is None
    assert h.resolve_tz("Not/AZone").key == "UTC"


def test_limits_and_envelope():
    assert h.clamp_limit(None, 20) == 20
    assert h.clamp_limit(500, 20) == 100
    assert h.clamp_limit("abc", 20) == 20
    assert h.clamp_offset(-5) == 0
    env = h.envelope([1, 2], total=5, offset=0, limit=2)
    assert env["has_more"] is True and env["next_offset"] == 2 and "incomplete" in env["note"]
    env = h.envelope([1, 2], total=2, offset=0, limit=2)
    assert env["has_more"] is False and "note" not in env


def test_dumps_trims_to_cap():
    payload = h.envelope([{"text": "y" * 1000} for _ in range(50)], total=50, offset=0, limit=50)
    out = json.loads(h.dumps(payload, cap=5000))
    assert out["has_more"] is True and out["count"] < 50 and "trimmed" in out["note"]
    assert len(h.dumps(payload, cap=5000)) <= 5000


def test_error_shape():
    err = json.loads(h.error("auth_failed", "nope", "fix it", retryable=False))
    assert err == {"ok": False, "error": {"code": "auth_failed", "message": "nope", "retryable": False, "hint": "fix it"}}
