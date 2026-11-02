"""Fetch RBI Master Directions: listing page -> detail page -> inline text."""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from collections.abc import Callable

import requests
from bs4 import BeautifulSoup

from . import store

log = logging.getLogger(__name__)

LISTING = "https://www.rbi.org.in/Scripts/BS_ViewMasterDirections.aspx"
DETAIL_URL = "https://rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id={id}"
DETAIL_PATH = "BS_ViewMasDirections.aspx"
HEADERS = {"User-Agent": "Mozilla/5.0 (reg-intel research; contact: owner)"}
DELAY = 2.0  # seconds between requests; be polite
TIMEOUT = 60  # seconds per request
MIN_TEXT_CHARS = 500  # shorter page text means the direction is PDF-only / not inline
SOURCE = "RBI"

Fetcher = Callable[[str], str]  # url -> response body; injected so fetch_one/run are testable offline


def _get(url: str) -> requests.Response:
    r = requests.get(url.strip(), headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    time.sleep(DELAY)
    return r


def fetch_html(url: str) -> str:
    """Default Fetcher: GET with a research User-Agent, raise on HTTP errors, sleep DELAY afterwards."""
    return _get(url).text


def parse_listing(html: str) -> list[dict]:
    """Master-direction links on the listing page as {id, url, title}, de-duplicated by id, in page order."""
    soup = BeautifulSoup(html, "html.parser")
    out, seen = [], set()
    for a in soup.select("a.link2"):
        href = (a.get("href") or "").strip()
        if DETAIL_PATH not in href:
            continue
        m = re.search(r"id=(\d+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        out.append({"id": m.group(1),
                    "url": DETAIL_URL.format(id=m.group(1)),
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


def fetch_one(db: sqlite3.Connection, item: dict, fetch: Fetcher = fetch_html) -> int | None:
    """Fetch one master direction into the store. Returns document id, or None if skipped.

    Skipped when the url is already stored or the inline text is shorter than MIN_TEXT_CHARS.
    """
    if db.execute("SELECT 1 FROM document WHERE url=?", (item["url"],)).fetchone():
        log.info("already stored: %s", item["url"])
        return None
    text = page_text(fetch(item["url"]))
    if len(text) < MIN_TEXT_CHARS:
        log.info("no inline text (%d chars): %s", len(text), item["url"])
        return None
    cur = db.execute("INSERT INTO document(source,url,title,sha256,raw_text,fetched_at) "
                     "VALUES(?,?,?,?,?,?)",
                     (SOURCE, item["url"], item["title"], store.sha(text), text, time.time()))
    db.commit()
    store.log_event(db, "fetch", item["url"], text)
    return cur.lastrowid


def run(limit: int = 5, db_path: str = store.DEFAULT_DB_PATH, fetch: Fetcher = fetch_html) -> None:
    """CLI entry: fetch the first ``limit`` listed directions, printing one fetched/skipped line each."""
    db = store.connect(db_path)
    for item in parse_listing(fetch(LISTING))[:limit]:
        did = fetch_one(db, item, fetch)
        print(("fetched " if did else "skipped ") + item["title"][:90])
