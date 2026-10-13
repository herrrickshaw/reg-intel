"""Fetch RBI Master Directions: listing page -> detail page -> inline text."""
from __future__ import annotations

import re
import time

import requests
from bs4 import BeautifulSoup

from . import store

LISTING = "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (reg-intel research; contact: owner)"}
DELAY = 2.0  # seconds between requests; be polite


def _get(url: str) -> requests.Response:
    r = requests.get(url.strip(), headers=HEADERS, timeout=60)
    r.raise_for_status()
    time.sleep(DELAY)
    return r


def parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select("a.link2"):
        href = (a.get("href") or "").strip()
        if "BS_ViewMasDirections.aspx" not in href:
            continue
        m = re.search(r"id=(\d+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append({"id": m.group(1),
                    "url": f"https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id={m.group(1)}",
                    "title": re.sub(r"\s+", " ", a.get_text()).strip()})
    return out


def page_text(detail_html: str) -> str:
    """Full direction text is inline in the largest table.tablebg. (The PDF host sits
    behind an anti-bot challenge, so we deliberately do not fetch PDFs.)"""
    soup = BeautifulSoup(detail_html, "html.parser")
    for t in soup(["script", "style"]):
        t.decompose()
    tables = soup.select("table.tablebg")
    if not tables:
        return ""
    best = max(tables, key=lambda t: len(t.get_text()))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", best.get_text("\n"))).strip()


def fetch_one(db, item: dict) -> int | None:
    """Fetch one master direction into the store. Returns document id, or None if skipped."""
    if db.execute("SELECT 1 FROM document WHERE url=?", (item["url"],)).fetchone():
        return None
    text = page_text(_get(item["url"]).text)
    if len(text) < 500:
        return None
    cur = db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) "
                     "VALUES('RBI',?,?,?,?,?)",
                     (item["url"], item["title"], store.sha(text), text, time.time()))
    db.commit()
    store.log_event(db, "fetch", item["url"], text)
    return cur.lastrowid


def run(limit: int = 5, db_path: str = "data/reg_intel.db") -> None:
    db = store.connect(db_path)
    for item in parse_listing(_get(LISTING).text)[:limit]:
        did = fetch_one(db, item)
        print(("fetched " if did else "skipped ") + item["title"][:90])
