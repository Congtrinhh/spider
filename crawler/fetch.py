"""HTTP: listing API + detail pages. created by tqcong, 29/08/2026"""

import logging
import time
from datetime import datetime

import httpx
from selectolax.parser import HTMLParser

from . import config

log = logging.getLogger("noxh.fetch")


class FetchError(Exception):
    """Raised when a request exhausts its retries."""


def make_client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.REQUEST_TIMEOUT_SEC,
        follow_redirects=True,
    )


def _sleep_politely():
    time.sleep(config.REQUEST_DELAY_SEC)


def request_with_retry(client: httpx.Client, method: str, url: str, **kwargs) -> httpx.Response:
    """GET/POST with exponential backoff on 5xx/timeout, MAX_RETRIES attempts."""
    last_exc = None
    for attempt in range(config.MAX_RETRIES):
        try:
            resp = client.request(method, url, **kwargs)
            if resp.status_code >= 500:
                raise httpx.HTTPStatusError(
                    f"server error {resp.status_code}", request=resp.request, response=resp
                )
            resp.raise_for_status()
            return resp
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < config.MAX_RETRIES - 1:
                backoff = config.REQUEST_DELAY_SEC * (2 ** attempt)
                log.warning("retry %d/%d for %s after error: %s (sleeping %.1fs)",
                            attempt + 1, config.MAX_RETRIES, url, exc, backoff)
                time.sleep(backoff)
    raise FetchError(f"exhausted {config.MAX_RETRIES} retries for {url}: {last_exc}") from last_exc


def fetch_listing_page(client: httpx.Client, page_index: int) -> str:
    """POST the listing API for one page. Returns the raw HTML fragment."""
    params = dict(config.site()["api_params"])
    data = {"PageIndex": str(page_index), **params}
    resp = request_with_retry(
        client, "POST", config.api_url(),
        data=data,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        },
    )
    _sleep_politely()
    return resp.text


def _parse_published_at(span) -> str | None:
    """span.news-time title='15:28, 28/08/2026' -> '2026-08-28'"""
    title = span.attributes.get("title") if span else None
    if not title:
        return None
    date_part = title.split(",")[-1].strip()
    try:
        return datetime.strptime(date_part, "%d/%m/%Y").date().isoformat()
    except ValueError:
        return None


def parse_listing(html: str) -> list[dict]:
    """Parse one listing fragment into item dicts."""
    tree = HTMLParser(html)
    items = []
    for node in tree.css("div.news-item"):
        data_id = node.attributes.get("data-id")
        link = node.css_first("a[href]")
        title_node = node.css_first("h3.news-title")
        time_node = node.css_first("span.news-time")
        img_node = node.css_first("img[src]")
        if not data_id or not link:
            continue
        items.append({
            "id": data_id,
            "url": config.abs_url(link.attributes.get("href", "")),
            "title": title_node.text(strip=True) if title_node else "",
            "published_at": _parse_published_at(time_node),
            "thumb": img_node.attributes.get("src") if img_node is not None else None,
        })
    return items


def crawl_listing(client: httpx.Client, seen_ids: set[str], full: bool = False, stats: dict | None = None):
    """Yield listing items page by page, applying the three stop conditions.
    `stats`, if given, is updated in place with {"pages": <pages fetched>}."""
    if stats is None:
        stats = {}
    stats["pages"] = 0

    page_index = 0
    while True:
        if page_index >= config.MAX_PAGES:
            log.warning(
                "MAX_PAGES (%d) reached at PageIndex=%d — the archive has outgrown "
                "the config; raise MAX_PAGES in crawler/config.py",
                config.MAX_PAGES, page_index,
            )
            return

        html = fetch_listing_page(client, page_index)

        # Past the last real page the API returns a genuinely empty body
        # (0 bytes, not an HTML fragment with zero items) — checked before
        # parsing, and before counting the page, so a probe past the end of
        # the archive doesn't inflate the reported page count.
        if not html.strip():
            log.info("page %d returned an empty response — end of archive", page_index)
            return

        items = parse_listing(html)
        stats["pages"] = page_index + 1

        if not items:
            log.info("page %d had no items — end of archive", page_index)
            return

        if not full and all(item["id"] in seen_ids for item in items):
            log.info("page %d fully seen — caught up (incremental mode)", page_index)
            return

        for item in items:
            yield item

        page_index += 1


def fetch_detail(client: httpx.Client, url: str) -> str:
    """Plain GET of a detail page. Raises FetchError on exhausted retries."""
    resp = request_with_retry(client, "GET", url)
    _sleep_politely()
    return resp.text
