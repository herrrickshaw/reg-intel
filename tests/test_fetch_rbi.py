"""reg_intel.fetch_rbi: listing/detail parsing and fetch_one with the HTTP call stubbed."""
from types import SimpleNamespace

from reg_intel import fetch_rbi, store

LISTING = """
<a class="link2" href="BS_ViewMasDirections.aspx?id=11">Master Direction A</a>
<a class="link2" href="BS_ViewMasDirections.aspx?id=11">duplicate</a>
<a class="link2" href="Other.aspx?id=12">not a direction</a>
<a href="BS_ViewMasDirections.aspx?id=13">no class</a>
<a class="link2" href="BS_ViewMasDirections.aspx?id=14">  Master
   Direction   B </a>"""


def test_parse_listing_dedupes_and_filters():
    out = fetch_rbi.parse_listing(LISTING)
    assert [o["id"] for o in out] == ["11", "14"]
    assert out[1]["title"] == "Master Direction B"
    assert out[0]["url"] == "https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11"


def test_page_text_strips_script_and_handles_missing_table():
    assert fetch_rbi.page_text("<html><p>no table</p></html>") == ""
    html = '<table class="tablebg"><tr><td><script>var x=1;</script>Banks shall comply.\n\n\nNext   line</td></tr></table>'
    assert fetch_rbi.page_text(html) == "Banks shall comply.\nNext line"


def test_fetch_one_stores_logs_and_skips(tmp_path, monkeypatch):
    body = '<table class="tablebg"><tr><td>' + "Banks shall comply. " * 40 + "</td></tr></table>"
    calls = []
    monkeypatch.setattr(fetch_rbi, "_get", lambda url: calls.append(url) or SimpleNamespace(text=body))
    db = store.connect(tmp_path / "t.db")
    item = {"url": "https://rbi.org.in/x?id=1", "title": "T"}
    assert fetch_rbi.fetch_one(db, item) == 1
    assert fetch_rbi.fetch_one(db, item) is None               # already stored: no second request
    assert len(calls) == 1
    assert db.execute("SELECT step FROM prov_event").fetchall() == [("fetch",)]
    monkeypatch.setattr(fetch_rbi, "_get", lambda url: SimpleNamespace(text="<table class='tablebg'><tr><td>short</td></tr></table>"))
    assert fetch_rbi.fetch_one(db, {"url": "u2", "title": "short"}) is None
