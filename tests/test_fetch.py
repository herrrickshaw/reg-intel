"""Fetch stage with an injected fetcher: no network, no RBI pages."""
from __future__ import annotations

from reg_intel import fetch_rbi, store

LONG_BODY = "Banks shall comply with every paragraph of this direction. " * 20  # > MIN_TEXT_CHARS


def _detail(body: str) -> str:
    nav = '<table class="tablebg"><tr><td>nav</td></tr></table>'
    return f'<table class="tablebg"><tr><td>{body}</td></tr></table>' + nav


def _listing(*ids: str) -> str:
    return "".join(
        f'<a class="link2" href=" https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id={i} ">MD {i}</a>'
        for i in ids)


def test_parse_listing_dedups_and_ignores_other_links():
    html = _listing("1", "2", "1") + '<a class="link2" href="/Scripts/Other.aspx?id=9">x</a>'
    got = fetch_rbi.parse_listing(html)
    assert [g["id"] for g in got] == ["1", "2"]
    assert got[0]["url"] == fetch_rbi.DETAIL_URL.format(id="1") and got[0]["title"] == "MD 1"


def test_page_text_empty_without_tablebg():
    assert fetch_rbi.page_text("<html><body><p>nothing</p></body></html>") == ""


def test_fetch_one_stores_logs_and_skips(tmp_path):
    db = store.connect(tmp_path / "t.db")
    calls = []

    def fetch(url):
        calls.append(url)
        return _detail(LONG_BODY)

    item = {"id": "1", "url": "https://rbi.org.in/x?id=1", "title": "MD 1"}
    did = fetch_rbi.fetch_one(db, item, fetch)
    assert did == 1 and calls == [item["url"]]
    src, title, sha = db.execute("SELECT source, title, sha256 FROM document WHERE id=1").fetchone()
    assert (src, title) == ("RBI", "MD 1") and len(sha) == 64
    assert db.execute("SELECT step FROM prov_event").fetchall() == [("fetch",)]
    assert store.verify_chain(db)
    # second call: already stored, fetcher not called again
    assert fetch_rbi.fetch_one(db, item, fetch) is None and len(calls) == 1
    # PDF-only page (short inline text) is skipped and not stored
    short = {"id": "2", "url": "https://rbi.org.in/x?id=2", "title": "MD 2"}
    assert fetch_rbi.fetch_one(db, short, lambda u: _detail("too short")) is None
    assert db.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 1


def test_run_respects_limit_and_prints(tmp_path, capsys):
    pages = {fetch_rbi.LISTING: _listing("1", "2", "3")}
    for i in "123":
        pages[fetch_rbi.DETAIL_URL.format(id=i)] = _detail(LONG_BODY)
    fetch_rbi.run(limit=2, db_path=tmp_path / "t.db", fetch=pages.__getitem__)
    out = capsys.readouterr().out.splitlines()
    assert out == ["fetched MD 1", "fetched MD 2"]
    assert store.connect(tmp_path / "t.db").execute("SELECT COUNT(*) FROM document").fetchone()[0] == 2
