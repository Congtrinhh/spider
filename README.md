# NOXH Finder

Triage tool for Hanoi social-housing (nhà ở xã hội) announcements published by Sở Xây dựng Hà Nội.

A nightly crawler fetches announcement posts, extracts five fields, and writes a static
`docs/projects.json`. A single-page frontend (`docs/index.html`) filters that JSON in the browser.

**This app is triage, not a source of truth.** Every row links to the original announcement.
Before submitting a hồ sơ, open the original page.

See [`IMPLEMENTATION.md`](./IMPLEMENTATION.md) for the full spec this was built against.

## Layout

```
crawler/         # Python crawler (config, fetch, extract, entry point)
docs/            # GitHub Pages root — index.html + projects.json
data/seen.json   # {data_id: iso_crawled_at}, drives incremental crawling
.github/workflows/crawl.yml   # nightly cron
```

## Running the crawler

```bash
pip install -r crawler/requirements.txt
python -m crawler.crawl          # incremental: only fetches posts not in data/seen.json
python -m crawler.crawl --full   # full rebuild: recrawls every post
```

Output is written to `docs/projects.json`; `data/seen.json` is updated alongside it. Both are
plain JSON — no database.

## Viewing the frontend locally

`docs/index.html` fetches `docs/projects.json` via `fetch()`, which most browsers block on a
bare `file://` URL. Serve the folder instead:

```bash
python -m http.server 8000 --directory docs
```

then open `http://localhost:8000`.

## Switching site / host

Everything host-specific lives in `crawler/config.py`. Changing `ACTIVE_SITE` to a profile added
under `SITES` is the only edit required to point the crawler at a different host (see the `TODO`
placeholder for a production profile — its `api_params` must be recaptured from that host's own
network tab, they are opaque per-host ciphertext).

## Known risks

- **`demo` host only.** Production is `soxaydung.hanoi.gov.vn`; its `api_params` almost certainly
  differ from the demo host's. Capture them before switching `ACTIVE_SITE`.
- **robots.txt.** Checked 29/08/2026: `soxaydung-demo.hanoi.gov.vn/robots.txt` returns
  `Allow: /` with `Crawl-delay: 5`. `config.REQUEST_DELAY_SEC` is set to `5.0` to honor that
  (the spec draft assumed `1.0`, pending this check). Re-check robots.txt on the production host
  before pointing the crawler at it — a stricter policy there should win over this default.
- **Wording drift.** Extraction patterns are anchored to current phrasing. The *Cần kiểm tra*
  count on the frontend is the early-warning signal for this — watch it; the spec expects ~10
  rows, not hundreds.
- **GitHub-runner IP blocks.** Untested against the CI runner's IP range — run `workflow_dispatch`
  manually at least once before trusting the nightly cron.

## Implementation notes

Built directly from `IMPLEMENTATION.md`, following its build order (config → fetch → extract,
validated against real fetched posts, before wiring `crawl.py` and the frontend). A few decisions
made while implementing, where the spec left room:

- `extract.to_lines()` scopes to `div.news-detail` rather than the whole `<body>`. The full body
  also contains the site nav, a "related articles" sidebar, and a footer with its own
  `Địa chỉ: ...` line and menu items containing words like `điều kiện` — left unscoped, those
  polluted `dia_diem` (picked up the *site's* contact address instead of the project's) and
  `luu_y` (picked up unrelated nav-menu text). Verified against four real detail pages fetched
  from the demo host.
- `gia_ban`/`gia_thue`/`gia_thue_mua` labels and values, and `dia_diem`'s primary anchor, are
  frequently on separate lines (table cells flatten to one line per cell) rather than
  `label: value` on one line — extraction checks the same line first, then falls back to the next
  line(s).
- Frontend list partitioning (spec §7): a project lands in exactly one of the three lists.
  `kind: "quarter"` always routes to *Sắp mở*, independent of `status` — per `extract.py`'s status
  rule, a quarter-only date can never produce `status: "parsed"`, so gating *Sắp mở* on `parsed`
  (as *Kết quả* is) would leave it permanently empty. *Cần kiểm tra* is intentionally unfiltered
  (no price/keyword/date filter applied) since it's a QA view, not a search result.
- Verified end-to-end (`python -m crawler.crawl --full` against 2 real listing pages, ~20 posts)
  against the live demo host: acceptance checks #3 and #4 from the spec pass — the Đồng Trúc -
  Thạch Thất post extracts `gia_ban: 23500000` (not the 178000 or 89000 rent figures on the same
  page) and `thoi_gian_nop_ho_so: {kind: "quarter", quarter: 3, year: 2026}`.
- `docs/projects.json` and `data/seen.json` in this repo hold that ~20-post smoke-test crawl, not
  the full archive (~101 pages at the time of writing). Run `python -m crawler.crawl --full`
  (it respects the 5s politeness delay, so a full run takes a while) or trigger the CI workflow
  manually to populate the rest.
