"""reg_intel.fetch_rbi: _get (requests faked) and run()."""
from types import SimpleNamespace

import pytest

from reg_intel import fetch_rbi, store


def test_get_strips_url_sets_headers_and_sleeps(monkeypatch):
    seen, slept = {}, []

    def fake_get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return SimpleNamespace(raise_for_status=lambda: seen.setdefault("checked", True), text="ok")

    monkeypatch.setattr(fetch_rbi.requests, "get", fake_get)
    monkeypatch.setattr(fetch_rbi.time, "sleep", slept.append)
    r = fetch_rbi._get("  http://x/y \n")
    assert r.text == "ok"
    assert seen == {"url": "http://x/y", "headers": fetch_rbi.HEADERS, "timeout": 60, "checked": True}
    assert slept == [fetch_rbi.DELAY]


def test_get_http_error_propagates_without_sleeping(monkeypatch):
    slept = []

    def boom():
        raise RuntimeError("404")

    monkeypatch.setattr(fetch_rbi.requests, "get", lambda *a, **k: SimpleNamespace(raise_for_status=boom))
    monkeypatch.setattr(fetch_rbi.time, "sleep", slept.append)
    with pytest.raises(RuntimeError):
        fetch_rbi._get("http://x")
    assert slept == []


def test_run_honours_limit_and_reports_fetched_vs_skipped(tmp_path, monkeypatch, capsys):
    listing = "".join(f'<a class="link2" href="BS_ViewMasDirections.aspx?id={i}">Direction {i}</a>' for i in range(1, 5))
    body = '<table class="tablebg"><tr><td>' + "Banks shall comply. " * 40 + "</td></tr></table>"
    got = []

    def fake_get(url):
        got.append(url)
        return SimpleNamespace(text=listing if url == fetch_rbi.LISTING else body)

    monkeypatch.setattr(fetch_rbi, "_get", fake_get)
    dbp = tmp_path / "sub" / "r.db"
    fetch_rbi.run(2, str(dbp))
    out = capsys.readouterr().out.splitlines()
    assert out == ["fetched Direction 1", "fetched Direction 2"]
    assert got[0] == fetch_rbi.LISTING and len(got) == 3      # listing + 2 details only
    fetch_rbi.run(3, str(dbp))                                # 1,2 already stored -> skipped, 3 new
    assert capsys.readouterr().out.splitlines() == ["skipped Direction 1", "skipped Direction 2", "fetched Direction 3"]
    assert store.connect(dbp).execute("SELECT COUNT(*) FROM document").fetchone()[0] == 3


def test_run_default_limit_is_five(tmp_path, monkeypatch, capsys):
    listing = "".join(f'<a class="link2" href="BS_ViewMasDirections.aspx?id={i}">D{i}</a>' for i in range(8))
    monkeypatch.setattr(fetch_rbi, "_get", lambda url: SimpleNamespace(text=listing if url == fetch_rbi.LISTING else "<p/>"))
    fetch_rbi.run(db_path=str(tmp_path / "r.db"))
    assert len(capsys.readouterr().out.splitlines()) == 5


def test_fetch_one_boundary_500_chars(tmp_path, monkeypatch):
    db = store.connect(tmp_path / "t.db")

    def page(n):
        return SimpleNamespace(text='<table class="tablebg"><tr><td>' + "a" * n + "</td></tr></table>")

    monkeypatch.setattr(fetch_rbi, "_get", lambda u: page(499))
    assert fetch_rbi.fetch_one(db, {"url": "u1", "title": "t"}) is None
    monkeypatch.setattr(fetch_rbi, "_get", lambda u: page(500))
    assert fetch_rbi.fetch_one(db, {"url": "u2", "title": "t"}) == 1


def test_politeness_delay_is_two_seconds():
    assert fetch_rbi.DELAY == 2.0


def test_run_truncates_printed_title_to_90_chars(tmp_path, monkeypatch, capsys):
    title = "T" * 100
    listing = f'<a class="link2" href="BS_ViewMasDirections.aspx?id=1">{title}</a>'
    monkeypatch.setattr(fetch_rbi, "_get", lambda url: SimpleNamespace(text=listing if url == fetch_rbi.LISTING else "<p/>"))
    fetch_rbi.run(1, str(tmp_path / "r.db"))
    assert capsys.readouterr().out == "skipped " + "T" * 90 + "\n"
