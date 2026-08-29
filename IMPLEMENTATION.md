# NOXH Finder — Implementation Spec

Triage tool for Hanoi social-housing (nhà ở xã hội) announcements published by Sở Xây dựng Hà Nội.

A nightly crawler fetches announcement posts, extracts five fields, and writes a static
`projects.json`. A single-page frontend filters that JSON in the browser.

**This app is triage, not a source of truth.** Every row links to the original announcement.
Before submitting a hồ sơ, the user opens the original page.

---

## 1. Non-goals

Do not build these. They are explicitly out of scope:

- No alerts, email, or push. On-demand search only.
- No user accounts, no login.
- No LLM API call at runtime. Extraction is pure regex, deterministic, offline.
- No server-side search endpoint. The frontend loads the whole JSON and filters client-side.
- No database. `projects.json` + `seen.json` are the entire persistence layer.
- No write operations against the Sở website. Read-only, GET/POST for retrieval only.

---

## 2. Repo layout

```
noxh-finder/
├── crawler/
│   ├── config.py          # ALL tunable values live here
│   ├── crawl.py           # entry point: fetch → extract → write JSON
│   ├── fetch.py           # HTTP: listing API + detail pages
│   ├── extract.py         # normalized text → fields
│   └── requirements.txt   # httpx, selectolax
├── docs/                  # GitHub Pages root
│   ├── index.html         # UI (single file: HTML + CSS + JS inline)
│   └── projects.json      # crawler output, committed by CI
├── data/
│   └── seen.json          # {data_id: iso_crawled_at} for incremental crawling
├── .github/workflows/
│   └── crawl.yml          # nightly cron
└── README.md
```

Stack: Python 3.11+, `httpx`, `selectolax`. Frontend is vanilla JS, no build step, no framework.

---

## 3. `crawler/config.py`

Everything host-specific and everything the user might tune goes here. Nothing below this file
should contain a hardcoded URL, page count, or threshold.

```python
"""Configuration. created by tqcong, 29/08/2026"""

# ---------------------------------------------------------------- site profiles
# The four base64 params are opaque ciphertext produced by the portal's own JS.
# We never decode them — we replay them. They are host-specific: when switching
# hosts, re-capture them from that host's network tab (POST to /api/NewsZone/NewsZone)
# and add a new profile below.
SITES = {
    "demo": {
        "base_url": "https://soxaydung-demo.hanoi.gov.vn",
        "listing_path": "/phat-trien-nha-o",
        "api_path": "/api/NewsZone/NewsZone",
        "api_params": {
            "PageSize":   "DxZD1w2i+8E=",
            "Catname":    "tcSrMdFFelkh9g86xUeP49VtbNmplxo1",
            "LanguageId": "jM2HDDVEz40=",
            "Site":       "t6dK76xLcwQ=",
        },
    },
    # TODO: capture params from the production host before switching.
    # "prod": {
    #     "base_url": "https://soxaydung.hanoi.gov.vn",
    #     ...
    # },
}

ACTIVE_SITE = "demo"          # <-- change this one line to switch hosts

# ---------------------------------------------------------------- crawl limits
# The portal exposes no total-page count, so we walk until a stop condition.
# 101 pages exist as of 29/08/2026; 200 leaves headroom. Raise if the guard
# in crawl.py starts firing.
MAX_PAGES = 200

REQUEST_DELAY_SEC = 1.0       # be polite: one request per second
REQUEST_TIMEOUT_SEC = 30
MAX_RETRIES = 3
USER_AGENT = "noxh-finder/1.0 (personal project; contact: <YOUR EMAIL>)"

# ---------------------------------------------------------------- paths
OUTPUT_JSON = "docs/projects.json"
SEEN_JSON = "data/seen.json"

# ---------------------------------------------------------------- extraction
PRICE_SANITY_MIN = 5_000_000    # đ/m² — below this, almost certainly a rent figure
PRICE_SANITY_MAX = 80_000_000   # đ/m² — above this, almost certainly a total price

# ---------------------------------------------------------------- UI defaults
# Written into projects.json meta so the frontend reads them from one place.
DEFAULT_MAX_PRICE = 20_000_000  # đ/m²
DEFAULT_DAYS_AHEAD = 30
# "closes"  → intake window still open at least N days from today (time to prepare)
# "opens"   → intake window starts at least N days from today (planning ahead)
DEFAULT_DATE_MODE = "closes"
```

**Helper** — resolve the active profile once:

```python
def site():
    return SITES[ACTIVE_SITE]

def api_url():
    return site()["base_url"] + site()["api_path"]

def abs_url(href):          # listing hrefs are root-relative
    return site()["base_url"] + href
```

---

## 4. Crawler (`fetch.py`)

### 4.1 Listing

Verified stateless — no cookies, no Referer, no Origin, no session needed.

```
POST {base_url}/api/NewsZone/NewsZone
Headers:
  X-Requested-With: XMLHttpRequest
  Content-Type: application/x-www-form-urlencoded; charset=UTF-8
Body (form-encoded):
  PageIndex=<n>&PageSize=…&Catname=…&LanguageId=…&Site=…
```

`PageIndex` is 0-based. `PageSize` is encrypted and therefore **not adjustable** — accept the
portal's default. Response is an **HTML fragment**, not JSON.

### 4.2 Listing item shape

```html
<div class="news-item" data-id="2661260828152802271">
  <div class="news-image"><a href="/phat-trien-nha-o/…-2661260828152802271.htm">
      <img src="https://sqhx-hanoi.mediacdn.vn/…"></a></div>
  <div class="news-info">
    <a href="…"><h3 class="news-title">Công khai thông tin …</h3></a>
    <span class="news-time" title="15:28, 28/08/2026">15:28 28/08/2026</span>
    <p class="news-sapo">Cụ thể như sau:</p>
  </div>
</div>
```

Parse with selectolax:

| Field | Source |
|---|---|
| `id` | `div.news-item[data-id]` |
| `url` | first `a[href]`, made absolute |
| `title` | `h3.news-title` text |
| `published_at` | `span.news-time` — parse `dd/mm/yyyy` from the `title` attribute |
| `thumb` | `img[src]` (optional, unused by UI) |

### 4.3 Pagination and stop conditions

Loop `PageIndex` from 0. Stop on the **first** of:

1. Fragment contains zero `div.news-item` → end of archive.
2. Every item on the page is already in `seen.json` → caught up (incremental mode).
3. `PageIndex` reaches `MAX_PAGES` → **log a loud warning**; the archive has outgrown the
   config and `MAX_PAGES` needs raising. Do not fail silently.

On a full rebuild (`--full` flag) skip condition 2.

### 4.4 Detail pages

Plain `GET`, no special headers beyond User-Agent. Fetch only IDs absent from `seen.json`.

**Fetch every new post. Never filter by slug keyword.** Title text is hand-typed and drifts —
one of the eight sample posts writes `tiepnhan` with no separator, which any `tiep-nhan` slug
filter would silently drop. That is precisely the failure mode this app exists to prevent.
`nha-o-xa-hoi` / `noxh` may be stored as a tag, never used as a gate.

### 4.5 Politeness

`REQUEST_DELAY_SEC` between all requests. Retry with exponential backoff on 5xx/timeout,
`MAX_RETRIES` attempts. Any post that still fails is emitted with `status: "failed"` and its
error message — never dropped.

---

## 5. Extraction (`extract.py`)

### 5.1 Normalize first

Detail pages are Word content pasted into a CMS. Some fields sit in
`div.VCSortableInPreviewMode[type=BoxTable]` tables, others in numbered prose ("10. Giá bán…").
**Do not special-case tables.** Flatten everything to text lines and run one label-anchored
path over the result.

```python
import unicodedata
from selectolax.parser import HTMLParser

def to_lines(html: str) -> list[str]:
    """HTML → normalized text lines. created by tqcong, 29/08/2026"""
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()
    text = tree.body.text(separator="\n")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ").replace("\u200b", "")
    text = text.replace("²", "2")           # unify m² / m2 / m<sup>2</sup>
    return [ln.strip() for ln in text.split("\n") if ln.strip()]
```

Keep the raw pre-normalization line for every extracted field — the UI shows it beside the value.

### 5.2 Fields

For each field, record `{value, raw_line, confidence}` where confidence is
`high` (label matched, value parsed) / `low` (heuristic fallback) / `null` (not found).

#### `gia_ban` — price per m²

**The critical trap.** A sample post carries three prices:

```
Giá bán căn hộ NOXH bình quân:        23.500.000 đồng/m2   ← the one we want
Giá cho thuê mua NOXH bình quân:         178.000 đồng/m2/tháng
Giá cho thuê căn hộ NOXH bình quân:       89.000 đồng/m2/tháng
```

A naive "first `đồng/m2` number" regex grabs **178.000**, passes a `< 20 triệu` filter, and
displays a 23.5tr project as cheap. Rules, in order:

1. The line must contain `giá bán` (case-insensitive, diacritics intact).
2. Reject the match if the unit is followed by `/tháng` or `/ tháng`.
3. Parse two number formats:
   - `23.500.000` → `.` is a thousands separator → `23500000`
   - `23,5 triệu` → `,` is a decimal comma, `triệu` = ×10⁶ → `23500000`
4. Reject values outside `[PRICE_SANITY_MIN, PRICE_SANITY_MAX]`; mark `low` confidence and
   keep the raw line so it surfaces in *cần kiểm tra*.

Also capture `gia_thue` and `gia_thue_mua` separately when present — they're useful, just not
filterable as sale price.

Note `(bao gồm VAT)` / `(tạm tính)` / `(chưa bao gồm phí bảo trì)` qualifiers and store them in
`luu_y`. Same number, different meaning.

#### `thoi_gian_nop_ho_so` — intake window

**Union type. Never coerce a quarter into a date.**

```json
{ "kind": "range",   "start": "2026-09-01", "end": "2026-10-15" }
{ "kind": "quarter", "quarter": 3, "year": 2026 }
{ "kind": "none" }
```

Label anchors: `Thời gian tiếp nhận hồ sơ`, `Kế hoạch tiếp nhận hồ sơ`, `thời hạn nộp hồ sơ`.

Patterns to handle:
- `từ ngày dd/mm/yyyy đến ngày dd/mm/yyyy` → `range`
- `Dự kiến trong Quý III năm 2026` → `quarter` (Roman numerals I–IV)
- `trong vòng 30 ngày kể từ ngày đăng tải` → `range`, computed from `published_at`,
  confidence `low` (the relative anchor is ambiguous)
- nothing matched → `none`

#### `dia_diem` — location

Anchors: `Địa điểm xây dựng`, `Địa chỉ`, `tại xã/phường/quận`. Fall back to extracting
`phường …` / `xã …` / `quận …` / `huyện …` from the **title**, at `low` confidence.

Note that Hanoi's administrative renaming appears inline — one sample reads
`phường Trần Phú, quận Hoàng Mai … nay thuộc phường Lĩnh Nam`. Store the whole string; do not
try to resolve old vs new names. The frontend does substring matching, which handles both.

#### `so_can_ban` — units for sale

Anchors: `số căn hộ`, `số lượng căn`, `căn hộ để bán`, `tổng số căn`. Extract the integer.
Beware totals that include rental units — if the line distinguishes bán / cho thuê /
thuê mua, take the *bán* figure only, else mark `low`.

#### `luu_y` — notes

Free-text collection, not a single value. Gather lines containing: `tạm tính`, `chưa bao gồm`,
`bao gồm VAT`, `phí bảo trì`, `đối tượng`, `điều kiện`, `chỉ nộp hồ sơ trực tiếp`,
`địa điểm duy nhất`. Cap at ~5 lines.

### 5.3 Record status

- `parsed` — `gia_ban` **and** `thoi_gian_nop_ho_so.kind != "none"`, both `high` confidence.
- `low_confidence` — found something, but any field is `low`, or the date is quarter-only.
- `failed` — fetch error, or neither price nor date found.

Everything reaches the JSON. Nothing is ever dropped.

---

## 6. Output — `docs/projects.json`

```json
{
  "meta": {
    "crawled_at": "2026-08-29T02:00:00+07:00",
    "site": "demo",
    "base_url": "https://soxaydung-demo.hanoi.gov.vn",
    "total": 1187,
    "counts": { "parsed": 340, "low_confidence": 122, "failed": 7 },
    "defaults": { "max_price": 20000000, "days_ahead": 30, "date_mode": "closes" }
  },
  "projects": [
    {
      "id": "2661260819142620941",
      "url": "https://…/cong-bo-…-266126081914262094.htm",
      "title": "Công bố công khai thông tin dự án … Đồng Trúc, Thạch Thất",
      "published_at": "2026-08-19",
      "status": "low_confidence",
      "dia_diem":    { "value": "xã Hà Bằng, thành phố Hà Nội", "raw_line": "…", "confidence": "high" },
      "so_can_ban":  { "value": null, "raw_line": null, "confidence": null },
      "gia_ban":     { "value": 23500000, "raw_line": "- Giá bán căn hộ NOXH bình quân: 23.500.000 đồng/m² (bao gồm VAT);", "confidence": "high" },
      "gia_thue":    { "value": 89000, "unit": "đ/m2/tháng", "raw_line": "…", "confidence": "high" },
      "thoi_gian_nop_ho_so": { "kind": "quarter", "quarter": 3, "year": 2026, "raw_line": "Dự kiến trong Quý III năm 2026", "confidence": "high" },
      "luu_y": ["Giá tạm tính, bao gồm VAT", "Chỉ nộp hồ sơ trực tiếp tại địa điểm duy nhất đã công bố"],
      "tags": ["noxh", "cong-khai-thong-tin"]
    }
  ]
}
```

Sort `projects` by `published_at` descending.

---

## 7. Frontend — `docs/index.html`

Single file. Vanilla JS. Fetches `projects.json` once on load, filters in memory. Vietnamese UI.

### Filters

| Control | Default (from `meta.defaults`) |
|---|---|
| Giá bán tối đa (đ/m²) | 20.000.000 |
| Số ngày tối thiểu | 30 |
| Chế độ ngày | "Còn hạn ít nhất N ngày" (`closes`) / "Bắt đầu sau N ngày" (`opens`) |
| Từ khóa địa điểm | empty — case- and diacritic-insensitive substring on `dia_diem` + `title` |

### Three lists — nothing hidden

1. **Kết quả** — `status: parsed`, `kind: "range"`, passing all filters.
2. **Sắp mở** — `kind: "quarter"`. Cannot be day-filtered; shown with the quarter label and
   subject only to the price and keyword filters. A quarter that has already ended is greyed,
   not removed.
3. **Cần kiểm tra** — `low_confidence` + `failed`. Title and link, plus whichever raw lines
   were captured. Expect ~10 rows, not hundreds.

Show a header count for each list so an unusually large *cần kiểm tra* signals wording drift.

### Row rendering

Each cell shows the parsed value with the raw source line beneath it in small grey text, so
verifying a deadline is one glance rather than reopening the original page. Every row's title
links to the original announcement. Put a permanent banner at the top:

> Công cụ này chỉ để lọc nhanh. Trước khi nộp hồ sơ, hãy mở thông báo gốc để kiểm tra.

---

## 8. CI — `.github/workflows/crawl.yml`

- `schedule: cron '0 19 * * *'` (02:00 Vietnam time) plus `workflow_dispatch`.
- Steps: checkout → setup-python → `pip install -r crawler/requirements.txt` →
  `python -m crawler.crawl` → commit `docs/projects.json` and `data/seen.json` if changed.
- Needs `permissions: contents: write`.
- Pages serves from `/docs` on `main`.

**Test `workflow_dispatch` manually before trusting the cron.** GitHub runners have foreign
IPs; if the Sở host blocks them, this whole approach fails and the crawler moves to the Hetzner
box (same code, run under cron, `rsync` the JSON out — or just serve it from that host).

---

## 9. Acceptance checks

1. `python -m crawler.crawl --full` completes without exception and reports a page count > 100.
2. If page 200 is reached, a `MAX_PAGES reached` warning appears in the log.
3. Post `…dong-truc-thach-that…266126081914262094.htm` extracts `gia_ban == 23500000`
   — **not** `178000` and **not** `89000`.
4. The same post yields `thoi_gian_nop_ho_so.kind == "quarter"`, `quarter == 3`, `year == 2026`
   — not a coerced calendar date.
5. `sum(counts.values()) == meta.total` — proving no record was dropped anywhere.
6. Changing `ACTIVE_SITE` is the only edit required to point at a different host.
7. Frontend loads with zero console errors and shows three lists with non-hidden counts.

---

## 10. Build order

1. `config.py` + `fetch.py` listing loop → print IDs and titles. Confirm > 100 pages.
2. Detail fetch + `to_lines()` → dump normalized text for the eight known sample posts.
3. `extract.py` against those eight. Get check #3 and #4 passing before writing anything else.
4. `crawl.py` wiring, `seen.json`, JSON output.
5. `index.html`.
6. CI last, once local runs are clean.

Step 3 is the real work. Steps 1, 2, 4, 6 are plumbing.

---

## 11. Known risks

- **`-demo` host.** Production is `soxaydung.hanoi.gov.vn`; `Site=` and probably `Catname=`
  differ there. Hanoi is also migrating `hanoi.gov.vn` → `thudo.gov.vn`, and the `thudo` host
  returned a WAF rejection page in earlier testing.
- **robots.txt.** An automated fetch of this host was refused on robots grounds during
  research, while the listing page's own meta tag says `index, follow`. Read the file and make
  the call before running the nightly job.
- **Wording drift.** Patterns are anchored to current phrasing. The *cần kiểm tra* count is the
  early-warning signal; watch it.
- **Two number formats and three unit variants** for price. Covered above, but the first thing
  to re-check when a value looks wrong.

---

## 12. Conventions

Mark added or changed code with a brief comment: `created/modified by tqcong, dd/mm/yyyy`.
When the implementation is complete, add a short change description to `README.md`.
