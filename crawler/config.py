"""Configuration. created by tqcong, 29/08/2026"""

import os

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

# robots.txt on the demo host (checked 29/08/2026) allows the crawl path but
# declares "Crawl-delay: 5" — honor it rather than the more aggressive 1s a
# generic polite-crawler default would use.
REQUEST_DELAY_SEC = 5.0
REQUEST_TIMEOUT_SEC = 30
MAX_RETRIES = 3
USER_AGENT = "noxh-finder/1.0 (personal project; contact: trinhquycong@gmail.com)"

# ---------------------------------------------------------------- paths
OUTPUT_JSON = "docs/projects.json"
SEEN_JSON = "data/seen.json"

# ---------------------------------------------------------------- extraction
PRICE_SANITY_MIN = 5_000_000    # đ/m² — below this, almost certainly a rent figure
PRICE_SANITY_MAX = 80_000_000   # đ/m² — above this, almost certainly a total price

# ---------------------------------------------------------------- relay (optional)
# TCP connections to the demo host are blocked from at least two independent
# hosting-provider ASNs (a GitHub-hosted runner and this project's own Hetzner
# VPS — see SELF_HOSTED_RUNNER.md), while direct connections work fine from
# non-datacenter-flagged networks. When RELAY_URL is set, requests are routed
# through a small Cloudflare Worker relay instead of connecting directly.
# Unset (the default) — nothing changes, connect straight to the site.
RELAY_URL = os.environ.get("NOXH_RELAY_URL")        # e.g. https://noxh-relay.<subdomain>.workers.dev
RELAY_SECRET = os.environ.get("NOXH_RELAY_SECRET")

# ---------------------------------------------------------------- UI defaults
# Written into projects.json meta so the frontend reads them from one place.
DEFAULT_MAX_PRICE = 20_000_000  # đ/m²
DEFAULT_DAYS_AHEAD = 30
# "closes"  → intake window still open at least N days from today (time to prepare)
# "opens"   → intake window starts at least N days from today (planning ahead)
DEFAULT_DATE_MODE = "closes"


def site():
    return SITES[ACTIVE_SITE]


def api_url():
    return site()["base_url"] + site()["api_path"]


def abs_url(href):          # listing hrefs are root-relative
    return site()["base_url"] + href
