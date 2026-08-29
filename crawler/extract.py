"""Normalized text lines -> structured fields. created by tqcong, 29/08/2026"""

import re
import unicodedata
from datetime import date, timedelta

from selectolax.parser import HTMLParser

from . import config

ROMAN_TO_INT = {"I": 1, "II": 2, "III": 3, "IV": 4}


def to_lines(html: str) -> list[str]:
    """HTML -> normalized text lines. created by tqcong, 29/08/2026"""
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()
    # Scope to the article container: the full <body> also carries the nav
    # menu, "related articles" sidebar, and site footer (which has its own
    # "Địa chỉ:" and menu items like "...đủ điều kiện...") — those pollute
    # dia_diem and luu_y with text that has nothing to do with the project.
    content = tree.css_first("div.news-detail") or tree.body
    text = content.text(separator="\n")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\xa0", " ").replace("​", "")
    text = text.replace("²", "2")           # unify m² / m2 / m<sup>2</sup>
    return [ln.strip() for ln in text.split("\n") if ln.strip()]


# ---------------------------------------------------------------- price parsing

_RE_TRIEU_PRICE = re.compile(r"(\d+(?:,\d+)?)\s*tri[eệ]u\s*(?:đ|đồng)", re.IGNORECASE)
_RE_THOUSANDS_PRICE = re.compile(r"(\d{1,3}(?:\.\d{3})+)\s*(?:đ|đồng)", re.IGNORECASE)
_RE_PLAIN_PRICE = re.compile(r"(\d{5,})\s*(?:đ|đồng)", re.IGNORECASE)
_RE_MONTHLY_UNIT = re.compile(r"/\s*th[aá]ng", re.IGNORECASE)


def _parse_price_number(line: str) -> int | None:
    m = _RE_TRIEU_PRICE.search(line)
    if m:
        return round(float(m.group(1).replace(",", ".")) * 1_000_000)
    m = _RE_THOUSANDS_PRICE.search(line)
    if m:
        return int(m.group(1).replace(".", ""))
    m = _RE_PLAIN_PRICE.search(line)
    if m:
        return int(m.group(1))
    return None


def extract_gia_ban(lines: list[str]) -> dict:
    """Sale price per m². The critical trap: reject monthly-rent lines even
    when they also contain the word 'giá' — only a literal 'giá bán' label
    with a non-monthly unit counts."""
    for line in lines:
        low = line.lower()
        if "giá bán" not in low:
            continue
        if _RE_MONTHLY_UNIT.search(low):
            continue
        num = _parse_price_number(line)
        if num is None:
            continue
        in_range = config.PRICE_SANITY_MIN <= num <= config.PRICE_SANITY_MAX
        return {"value": num, "raw_line": line, "confidence": "high" if in_range else "low"}
    return {"value": None, "raw_line": None, "confidence": None}


def _extract_rent_price(lines: list[str], mua: bool) -> dict:
    """gia_thue_mua (mua=True) or gia_thue (mua=False). 'cho thuê mua' is a
    substring of itself but never of plain 'cho thuê', so exclude it there."""
    for line in lines:
        low = line.lower()
        if "giá" not in low:
            continue
        if mua:
            if "thuê mua" not in low:
                continue
        else:
            if "cho thuê mua" in low or "cho thuê" not in low:
                continue
        num = _parse_price_number(line)
        if num is None:
            continue
        unit = "đ/m2/tháng" if _RE_MONTHLY_UNIT.search(low) else "đ/m2"
        return {"value": num, "unit": unit, "raw_line": line, "confidence": "high"}
    return {"value": None, "unit": None, "raw_line": None, "confidence": None}


# ---------------------------------------------------------------- date parsing

_DATE_ANCHORS = ("thời gian tiếp nhận hồ sơ", "kế hoạch tiếp nhận hồ sơ", "thời hạn nộp hồ sơ")

_RE_RANGE = re.compile(
    r"từ\s*ngày\s*(\d{1,2}/\d{1,2}/\d{4}).{0,40}?đến\s*ngày\s*(\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)
_RE_QUARTER = re.compile(r"qu[ýy]\s+(IV|III|II|I)\s*(?:năm)?\s*(\d{4})", re.IGNORECASE)
_RE_RELATIVE = re.compile(
    r"trong\s*vòng\s*(\d+)\s*ngày\s*kể\s*từ\s*ngày\s*đăng\s*tải", re.IGNORECASE
)


def _parse_ddmmyyyy(s: str) -> date:
    d, m, y = (int(x) for x in s.split("/"))
    return date(y, m, d)


def _try_date_patterns(line: str, published_at: str | None) -> dict | None:
    m = _RE_RANGE.search(line)
    if m:
        return {
            "kind": "range",
            "start": _parse_ddmmyyyy(m.group(1)).isoformat(),
            "end": _parse_ddmmyyyy(m.group(2)).isoformat(),
            "raw_line": line,
            "confidence": "high",
        }
    m = _RE_QUARTER.search(line)
    if m:
        return {
            "kind": "quarter",
            "quarter": ROMAN_TO_INT[m.group(1).upper()],
            "year": int(m.group(2)),
            "raw_line": line,
            "confidence": "high",
        }
    m = _RE_RELATIVE.search(line)
    if m and published_at:
        days = int(m.group(1))
        start = date.fromisoformat(published_at)
        end = start + timedelta(days=days)
        return {
            "kind": "range",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "raw_line": line,
            "confidence": "low",
        }
    return None


def extract_thoi_gian_nop_ho_so(lines: list[str], published_at: str | None) -> dict:
    """Union type: range | quarter | none. Never coerce a quarter into a date."""
    for i, line in enumerate(lines):
        low = line.lower()
        if not any(a in low for a in _DATE_ANCHORS):
            continue
        for cand in lines[i:i + 3]:
            result = _try_date_patterns(cand, published_at)
            if result:
                return result
    return {"kind": "none", "raw_line": None, "confidence": None}


# ---------------------------------------------------------------- location

_LOCATION_PRIMARY = ("địa điểm xây dựng",)
_LOCATION_SECONDARY = ("địa chỉ",)
_LOCATION_EXCLUDE = ("liên hệ", "nộp hồ sơ", "tiếp nhận hồ sơ")
_RE_TITLE_LOCATION = re.compile(r"(phường|xã|quận|huyện)\s+[^,;.()]+", re.IGNORECASE)


def _value_after_label(lines: list[str], i: int) -> tuple[str | None, str | None]:
    """Same-line remainder after ':', else the next non-empty line — labels
    in the source CMS are as often table-cell-split as inline."""
    line = lines[i]
    if ":" in line:
        after = line.split(":", 1)[1].strip(" .;")
        if after:
            return after, line
    if i + 1 < len(lines):
        return lines[i + 1].strip(" .;"), lines[i + 1]
    return None, None


def extract_dia_diem(lines: list[str], title: str) -> dict:
    for i, line in enumerate(lines):
        if any(a in line.lower() for a in _LOCATION_PRIMARY):
            val, raw = _value_after_label(lines, i)
            if val:
                return {"value": val, "raw_line": raw, "confidence": "high"}
    for i, line in enumerate(lines):
        low = line.lower()
        # 'Địa chỉ liên hệ và địa chỉ nộp hồ sơ' is the submission office,
        # not the build site — exclude it so it never masquerades as dia_diem.
        if any(a in low for a in _LOCATION_SECONDARY) and not any(x in low for x in _LOCATION_EXCLUDE):
            val, raw = _value_after_label(lines, i)
            if val:
                return {"value": val, "raw_line": raw, "confidence": "high"}
    m = _RE_TITLE_LOCATION.search(title)
    if m:
        return {"value": m.group(0).strip(), "raw_line": title, "confidence": "low"}
    return {"value": None, "raw_line": None, "confidence": None}


# ---------------------------------------------------------------- units for sale

_RE_INT_CAN = re.compile(r"(\d[\d.]*\d|\d)\s*căn")


def _parse_int_can(line: str) -> int | None:
    m = _RE_INT_CAN.search(line)
    if not m:
        return None
    return int(m.group(1).replace(".", ""))


def extract_so_can_ban(lines: list[str]) -> dict:
    for line in lines:
        low = line.lower()
        if "để bán" in low and "căn" in low:
            n = _parse_int_can(line)
            if n is not None:
                return {"value": n, "raw_line": line, "confidence": "high"}
    for line in lines:
        low = line.lower()
        if any(a in low for a in ("tổng số căn", "số lượng căn", "số căn hộ")):
            n = _parse_int_can(line)
            if n is not None:
                return {"value": n, "raw_line": line, "confidence": "low"}
    return {"value": None, "raw_line": None, "confidence": None}


# ---------------------------------------------------------------- notes

_LUU_Y_KEYWORDS = (
    "tạm tính", "chưa bao gồm", "bao gồm vat", "phí bảo trì",
    "đối tượng", "điều kiện", "chỉ nộp hồ sơ trực tiếp", "địa điểm duy nhất",
)
_LUU_Y_CAP = 5


def extract_luu_y(lines: list[str]) -> list[str]:
    found = []
    for line in lines:
        low = line.lower()
        if any(k in low for k in _LUU_Y_KEYWORDS) and line not in found:
            found.append(line)
        if len(found) >= _LUU_Y_CAP:
            break
    return found


# ---------------------------------------------------------------- record status


def _determine_status(gia_ban: dict, thoi_gian: dict) -> str:
    has_price = gia_ban["value"] is not None
    has_date = thoi_gian["kind"] != "none"

    if not has_price and not has_date:
        return "failed"

    if (
        has_price and has_date
        and gia_ban["confidence"] == "high"
        and thoi_gian["confidence"] == "high"
        and thoi_gian["kind"] == "range"          # a quarter can't be day-filtered
    ):
        return "parsed"

    return "low_confidence"


# ---------------------------------------------------------------- entry point


def extract_fields(html: str, title: str, published_at: str | None) -> dict:
    lines = to_lines(html)

    gia_ban = extract_gia_ban(lines)
    gia_thue = _extract_rent_price(lines, mua=False)
    gia_thue_mua = _extract_rent_price(lines, mua=True)
    thoi_gian = extract_thoi_gian_nop_ho_so(lines, published_at)
    dia_diem = extract_dia_diem(lines, title)
    so_can_ban = extract_so_can_ban(lines)
    luu_y = extract_luu_y(lines)

    return {
        "status": _determine_status(gia_ban, thoi_gian),
        "dia_diem": dia_diem,
        "so_can_ban": so_can_ban,
        "gia_ban": gia_ban,
        "gia_thue": gia_thue,
        "gia_thue_mua": gia_thue_mua,
        "thoi_gian_nop_ho_so": thoi_gian,
        "luu_y": luu_y,
    }
