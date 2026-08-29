"""Entry point: fetch -> extract -> write JSON. created by tqcong, 29/08/2026"""

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, extract, fetch

log = logging.getLogger("noxh.crawl")

_VN_TZ = timezone(timedelta(hours=7))


def _now_iso() -> str:
    return datetime.now(_VN_TZ).isoformat(timespec="seconds")


def _empty_field() -> dict:
    return {"value": None, "raw_line": None, "confidence": None}


def _empty_rent_field() -> dict:
    return {"value": None, "unit": None, "raw_line": None, "confidence": None}


def _tags(title: str) -> list[str]:
    tags = ["noxh"]
    if "công khai" in title.lower():
        tags.append("cong-khai-thong-tin")
    return tags


def load_seen() -> dict:
    path = Path(config.SEEN_JSON)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_seen(seen: dict) -> None:
    path = Path(config.SEEN_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def load_existing_projects() -> dict:
    path = Path(config.OUTPUT_JSON)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {p["id"]: p for p in data.get("projects", [])}


def build_record(item: dict, client) -> dict:
    """Fetch + extract one post. Never drop it — a fetch error still yields
    a record, with status 'failed' and the error message attached."""
    base = {
        "id": item["id"],
        "url": item["url"],
        "title": item["title"],
        "published_at": item["published_at"],
        "crawled_at": _now_iso(),
        "tags": _tags(item["title"]),
    }

    try:
        html = fetch.fetch_detail(client, item["url"])
    except fetch.FetchError as exc:
        log.warning("fetch failed for %s: %s", item["id"], exc)
        return {
            **base,
            "status": "failed",
            "error": str(exc),
            "dia_diem": _empty_field(),
            "so_can_ban": _empty_field(),
            "gia_ban": _empty_field(),
            "gia_thue": _empty_rent_field(),
            "gia_thue_mua": _empty_rent_field(),
            "thoi_gian_nop_ho_so": {"kind": "none", "raw_line": None, "confidence": None},
            "luu_y": [],
        }

    fields = extract.extract_fields(html, item["title"], item["published_at"])
    return {**base, **fields}


def run(full: bool = False) -> dict:
    seen = load_seen()
    projects_by_id = {} if full else load_existing_projects()

    client = fetch.make_client()
    stats: dict = {}
    new_count = 0
    try:
        for item in fetch.crawl_listing(client, set(seen.keys()), full=full, stats=stats):
            if not full and item["id"] in seen:
                continue  # already crawled; keep the existing record as-is
            record = build_record(item, client)
            projects_by_id[item["id"]] = record
            seen[item["id"]] = record["crawled_at"]
            new_count += 1
    finally:
        client.close()

    log.info("listing walk covered %d page(s)", stats.get("pages", 0))

    projects = sorted(projects_by_id.values(), key=lambda p: p["published_at"] or "", reverse=True)

    counts = {"parsed": 0, "low_confidence": 0, "failed": 0}
    for p in projects:
        counts[p["status"]] = counts.get(p["status"], 0) + 1
    assert sum(counts.values()) == len(projects), "a record was dropped somewhere"

    output = {
        "meta": {
            "crawled_at": _now_iso(),
            "site": config.ACTIVE_SITE,
            "base_url": config.site()["base_url"],
            "total": len(projects),
            "counts": counts,
            "defaults": {
                "max_price": config.DEFAULT_MAX_PRICE,
                "days_ahead": config.DEFAULT_DAYS_AHEAD,
                "date_mode": config.DEFAULT_DATE_MODE,
            },
        },
        "projects": projects,
    }

    out_path = Path(config.OUTPUT_JSON)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    save_seen(seen)

    log.info("crawl complete: %d new/updated, %d total, counts=%s", new_count, len(projects), counts)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="NOXH Finder crawler")
    parser.add_argument("--full", action="store_true",
                         help="full rebuild: ignore seen.json, recrawl every post")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    run(full=args.full)


if __name__ == "__main__":
    main()
