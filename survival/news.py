"""Headlines from Google News RSS. Free, no key, no full articles: enough to know what is happening,
small enough that reading it does not bankrupt the agent."""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from typing import Any

import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
_TAG = re.compile(r"<[^>]+>")


def _clean(text: str | None, limit: int) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def search_news(query: str, days: int = 3, limit: int = 8, session: requests.Session | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    days = max(1, min(int(days), 30))
    limit = max(1, min(int(limit), 15))
    http = session or requests.Session()
    resp = http.get(
        GOOGLE_NEWS_RSS,
        params={"q": f"{query} when:{days}d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (survival-agent)"},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.iter("item"):
        source = item.find("source")
        items.append({
            "title": _clean(item.findtext("title"), 200),
            "source": _clean(source.text if source is not None else "", 60),
            "published": _clean(item.findtext("pubDate"), 40),
            "snippet": _clean(item.findtext("description"), 240),
        })
        if len(items) >= limit:
            break
    return items
