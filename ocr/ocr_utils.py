"""
ocr/ocr_utils.py

Handles all OCR processing for Akshaya Vistara bill automation.

Pipeline:
  1. Accept uploaded file (image or PDF)
  2. Convert PDF pages → PIL Images via pdf2image
  3. Pre-process images (grayscale, denoise) for better accuracy
  4. Run pytesseract on each page
  5. Extract structured fields with regex + smart fallbacks
  6. Return raw_text + parsed dict

Field Extraction:
  - GSTIN        → 15-char alphanumeric pattern (Indian GST standard)
  - Date         → Multiple date formats (dd/mm/yyyy, yyyy-mm-dd, etc.)
  - Total Amount → Lines containing "total", "grand total", "amount due"
  - Vendor Name  → First non-empty line OR "From:" / "Billed by:" heuristic

Windows path for Tesseract is auto-detected or falls back to default install location.
"""

import re
import os
import logging
from decimal import Decimal, InvalidOperation
from typing import Optional

from PIL import Image, ImageFilter, ImageEnhance

logger = logging.getLogger(__name__)

# ── Tesseract path (Windows) ──────────────────────────────────────────────
TESSERACT_DEFAULT_WINDOWS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_DEFAULT_LINUX   = "/usr/bin/tesseract"

try:
    import pytesseract

    # Windows: set the executable path explicitly
    if os.name == "nt":
        tess_path = os.environ.get("TESSERACT_PATH", TESSERACT_DEFAULT_WINDOWS)
        pytesseract.pytesseract.tesseract_cmd = tess_path

    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    logger.warning("pytesseract not installed — OCR disabled.")

try:
    from pdf2image import convert_from_path, convert_from_bytes

    # Windows: point pdf2image to the Poppler bin folder
    POPPLER_PATH = os.environ.get(
        "POPPLER_PATH",
        r"C:\poppler\Library\bin",  # default after Poppler Windows ZIP extraction
    )
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False
    logger.warning("pdf2image not installed — PDF OCR disabled.")


# ─────────────────────────────────────────────────────────────────────────────
# REGEX PATTERNS
# ─────────────────────────────────────────────────────────────────────────────

# GSTIN: 2 digits + 5 uppercase letters + 4 digits + 1 letter + 1 alphanumeric + Z + 1 alphanumeric
GSTIN_PATTERN = re.compile(
    r"\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b"
)

GSTIN_NEAR_MATCH_PATTERN = re.compile(r"\b([0-9A-Z]{14,18})\b")

# Date patterns (ordered from most specific to least)
_MONTH_NAMES = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)

DATE_PATTERNS = [
    # ISO: 2024-03-15
    re.compile(r"\b(\d{4}[-/]\d{2}[-/]\d{2})\b"),
    # dd/mm/yyyy or dd-mm-yyyy (numeric only)
    re.compile(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{4})\b"),
    # dd-Mon-yyyy  e.g. 15-Mar-2024  (dash-separated with abbreviated month)
    re.compile(
        r"\b(\d{1,2}[-/]" + _MONTH_NAMES + r"[-/]\d{4})\b",
        re.IGNORECASE,
    ),
    # dd Month yyyy  e.g.  15 March 2024  (space-separated)
    re.compile(
        r"\b(\d{1,2}\s+" + _MONTH_NAMES + r"\s+\d{4})\b",
        re.IGNORECASE,
    ),
    # Month dd, yyyy  e.g.  March 15, 2024
    re.compile(
        r"\b(" + _MONTH_NAMES + r"\s+\d{1,2},?\s+\d{4})\b",
        re.IGNORECASE,
    ),
]

# Amount keywords that signal the grand total line
TOTAL_KEYWORDS = re.compile(
    r"(?:grand\s+total|total\s+amount|net\s+amount|amount\s+due|"
    r"amount\s+payable|invoice\s+total|total\s+payable|total\s+due|"
    r"\btotal\b)",
    re.IGNORECASE,
)

# Amount value: optional ₹/Rs/INR + digits with commas/decimals
AMOUNT_PATTERN = re.compile(
    r"(?:[₹]|Rs\.?|INR)?\s*(\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?|\d{4,}(?:\.\d{1,2})?)"
)

# Vendor-name heuristics
VENDOR_LABEL_PATTERN = re.compile(
    r"(?:from|billed\s+by|sold\s+by|seller|vendor|supplier|issued\s+by|bill\s+from|invoice\s+from)[:\s]+(.+)",
    re.IGNORECASE,
)

# Bill / invoice number patterns
BILL_NUMBER_PATTERN = re.compile(
    r"(?:invoice\s*(?:no|num|number|#)|bill\s*(?:no|num|number|#)|"
    r"receipt\s*(?:no|num|number|#)|ref\s*(?:no|num|number|#)|"
    r"voucher\s*(?:no|num|number|#))[:\s]*([A-Z0-9][A-Z0-9/_\-]{2,20})",
    re.IGNORECASE,
)

COMPANY_NAME_HINTS = (
    "LIMITED", "LTD", "LLP", "PVT", "PRIVATE", "ENTERPRISES", "TRADERS",
    "TRADING", "RETAIL", "STORE", "INDUSTRIES", "INDUSTRY", "AGENCIES",
    "AGENCY", "SUPPLIERS", "SUPPLIER", "COMPANY", "CO", "SERVICES",
)

GSTIN_DIGIT_OCR_MAP = {
    "O": "0",
    "Q": "0",
    "D": "0",
    "I": "1",
    "L": "1",
    "S": "5",
    "Z": "2",
}

GSTIN_ALPHA_OCR_MAP = {
    "0": "O",
    "1": "I",
    "5": "S",
    "2": "Z",
    "8": "B",
}


# ─────────────────────────────────────────────────────────────────────────────
# IMAGE PRE-PROCESSING
# ─────────────────────────────────────────────────────────────────────────────

def preprocess_image(img: Image.Image) -> Image.Image:
    """
    Convert to grayscale, increase contrast, and apply a slight sharpen.
    This significantly improves Tesseract accuracy on low-quality scans.
    """
    img = img.convert("L")                               # grayscale
    img = ImageEnhance.Contrast(img).enhance(2.0)        # boost contrast
    img = img.filter(ImageFilter.SHARPEN)                # sharpen edges
    return img



def _score_ocr_text_candidate(text: str) -> tuple[int, int]:
    """Prefer OCR output that produces more complete structured fields."""
    if not text.strip():
        return (0, 0)
    try:
        parsed = parse_fields(text)
        score = parsed.get("confidence_score", 0)
        if parsed.get("vendor_name"):
            score += 20
        if parsed.get("bill_number"):
            score += 10
        return (score, len(text))
    except Exception:
        return (0, len(text))



def _ocr_with_best_psm(img: Image.Image, psm_values: tuple[int, ...] = (6, 4, 11)) -> str:
    """Run Tesseract with several PSM modes and keep the best parsed result."""
    best_text = ""
    best_score = (-1, -1)

    for psm in psm_values:
        text = pytesseract.image_to_string(img, config=f"--oem 3 --psm {psm}", lang="eng").strip()
        score = _score_ocr_text_candidate(text)
        if score > best_score:
            best_score = score
            best_text = text

    return best_text


# ─────────────────────────────────────────────────────────────────────────────
# CORE OCR FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def extract_text_from_file(file_path: str) -> tuple[str, Optional[str]]:
    """
    Run OCR on a file (image or PDF).

    Returns:
        (raw_text, error_message)
        On success → (text, None)
        On failure → ("", error_string)
    """
    if not TESSERACT_AVAILABLE:
        return "", "pytesseract is not installed. Please install it: pip install pytesseract"

    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".pdf":
            return _ocr_pdf(file_path)
        else:
            return _ocr_image(file_path)
    except Exception as exc:
        logger.exception("OCR failed for file: %s", file_path)
        return "", str(exc)


def _ocr_image(file_path: str) -> tuple[str, None]:
    """Run Tesseract on a single image file."""
    img = Image.open(file_path)
    # Upscale small images so Tesseract has more pixels to work with.
    w, h = img.size
    if w < 1600:
        scale = max(2, 1600 // w)
        img = img.resize((w * scale, h * scale), Image.LANCZOS)
    img = preprocess_image(img)
    text = _ocr_with_best_psm(img)
    return text.strip(), None


def _ocr_pdf(file_path: str) -> tuple[str, Optional[str]]:
    """Convert PDF pages to images then run Tesseract on each."""
    if not PDF2IMAGE_AVAILABLE:
        return "", (
            "pdf2image is not installed, or Poppler is not on PATH. "
            "Install Poppler and set POPPLER_PATH in .env."
        )

    # Determine poppler_path for Windows
    # Higher DPI → more pixels → better Tesseract accuracy for invoices
    kwargs = {"dpi": 400, "fmt": "png"}
    if os.name == "nt":
        poppler_path = os.environ.get("POPPLER_PATH", r"C:\poppler\Library\bin")
        if os.path.isdir(poppler_path):
            kwargs["poppler_path"] = poppler_path

    pages = convert_from_path(file_path, **kwargs)

    all_text = []
    for i, page in enumerate(pages, start=1):
        page = preprocess_image(page)
        # PSM 4: single column with varying sizes — better for invoice layouts
        custom_config = r"--oem 3 --psm 4"
        page_text = pytesseract.image_to_string(page, config=custom_config, lang="eng")
        all_text.append(f"--- Page {i} ---\n{page_text.strip()}")

    return "\n\n".join(all_text), None


# ─────────────────────────────────────────────────────────────────────────────
# FIELD EXTRACTION
# ─────────────────────────────────────────────────────────────────────────────

def _normalise_date(raw: str) -> str:
    """
    Try to parse a raw date string into ISO format (YYYY-MM-DD).
    Falls back to returning the raw string unchanged.
    """
    from datetime import datetime
    formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d",
        # dd-Mon-yyyy  e.g. 15-Mar-2024
        "%d-%b-%Y", "%d/%b/%Y",
        # dd Month yyyy (full and abbreviated)
        "%d %B %Y", "%d %b %Y",
        # Month dd, yyyy
        "%B %d, %Y", "%b %d, %Y",
        "%d %B, %Y", "%d %b, %Y",
    ]
    cleaned = raw.strip().replace(",", "")
    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


def _clean_company_candidate(line: str) -> str:
    """Strip label noise while preserving readable company names."""
    line = re.sub(r"^[^\w]+|[^\w)]+$", "", line.strip())
    return re.sub(r"\s+", " ", line).strip(" :-|")


def _looks_like_company_name(line: str) -> bool:
    """Heuristic for strong header-style vendor names near the top of the bill."""
    cleaned = _clean_company_candidate(line)
    if len(cleaned) < 4 or len(cleaned) > 80:
        return False
    if any(ch.isdigit() for ch in cleaned):
        return False
    if AMOUNT_PATTERN.search(cleaned):
        return False
    words = [w for w in re.split(r"\s+", cleaned.upper()) if w]
    if len(words) < 2:
        return False
    return any(hint in words for hint in COMPANY_NAME_HINTS)


def _normalise_gstin_candidate(token: str) -> str:
    """Normalize OCR-noisy GSTIN candidates using position-aware rules."""
    token = re.sub(r"[^A-Z0-9]", "", token.upper())
    if len(token) != 15:
        return token

    digit_positions = {0, 1, 7, 8, 9, 10, 12, 14}
    alpha_positions = {2, 3, 4, 5, 6, 11}
    chars = []
    for idx, ch in enumerate(token):
        if idx in digit_positions:
            chars.append(GSTIN_DIGIT_OCR_MAP.get(ch, ch))
        elif idx in alpha_positions:
            chars.append(GSTIN_ALPHA_OCR_MAP.get(ch, ch))
        elif idx == 13:
            chars.append("Z" if ch in {"2", "7", "Z"} else ch)
        else:
            chars.append(ch)
    return "".join(chars)


def _extract_gstin_with_fallback(raw_text: str, lines: list[str]) -> str:
    """
    Recover GSTIN from OCR-noisy GST lines.

    We first try strict regex on uppercased text. If that fails, inspect lines that
    mention GSTIN/GST and normalize common OCR confusions before validating.
    """
    raw_text_upper = raw_text.upper()
    strict_matches = GSTIN_PATTERN.findall(raw_text_upper)
    if strict_matches:
        return strict_matches[0]

    for line in lines:
        if "GSTIN" not in line.upper() and not re.search(r"\bGST\b", line, re.IGNORECASE):
            continue

        tail = re.split(r"GSTIN|GST\s*(?:NO|NUMBER)?", line, maxsplit=1, flags=re.IGNORECASE)[-1]
        compact = re.sub(r"[^A-Z0-9]", "", tail.upper())

        direct = GSTIN_PATTERN.search(compact)
        if direct:
            return direct.group(1)

        for token in GSTIN_NEAR_MATCH_PATTERN.findall(compact):
            for start in range(0, max(1, len(token) - 14)):
                candidate = _normalise_gstin_candidate(token[start:start + 15])
                if len(candidate) == 15 and GSTIN_PATTERN.fullmatch(candidate):
                    return candidate

    return ""


def parse_fields(raw_text: str) -> dict:
    """
    Extract structured fields from raw OCR text for Indian invoices.

    Returns a dict with keys:
        vendor_name      str | ""
        gstin            str | ""
        date             str | ""   (ISO YYYY-MM-DD when parsed)
        total_amount     str | ""   (numeric string, e.g. "12500.00")
        bill_number      str | ""   (invoice/bill reference number)
        all_amounts      list[str]  (all amounts found, for manual selection)
        raw_lines        list[str]  (first 10 non-empty lines for debugging)
        confidence_score int        (0-100; pct of key fields found)
        low_confidence   bool       (True when confidence_score < 70)
    """
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    result = {
        "vendor_name":      "",
        "gstin":            "",
        "date":             "",
        "total_amount":     "",
        "bill_number":      "",
        "all_amounts":      [],
        "raw_lines":        lines[:10],
        "confidence_score": 0,
        "low_confidence":   True,
    }

    if not lines:
        return result

    # -- Normalise for uppercase-dependent patterns ---------------------------
    # Tesseract sometimes lowercases characters (especially in low-quality scans).
    # GSTIN regex requires uppercase letters, so we match against the uppercased
    # version of the text while keeping the original for everything else.
    raw_text_upper = raw_text.upper()

    # -- GSTIN ----------------------------------------------------------------
    result["gstin"] = _extract_gstin_with_fallback(raw_text, lines)

    # -- Date -----------------------------------------------------------------
    from datetime import date as _date_type
    _today = _date_type.today()
    _max_year = _today.year + 1   # reject dates more than 1 year in the future

    for pattern in DATE_PATTERNS:
        m = pattern.search(raw_text)
        if m:
            iso = _normalise_date(m.group(1))
            # Plausibility check: reject obviously wrong future years and very old dates
            try:
                parsed_dt = __import__("datetime").datetime.strptime(iso, "%Y-%m-%d").date()
                if 2000 <= parsed_dt.year <= _max_year:
                    result["date"] = iso
                    break
                # else: keep searching — this match is implausible
            except ValueError:
                result["date"] = iso   # non-standard format, keep as-is
                break

    # -- Bill number ----------------------------------------------------------
    bill_match = BILL_NUMBER_PATTERN.search(raw_text)
    if bill_match:
        result["bill_number"] = bill_match.group(1).strip().upper()

    # -- Total Amount ---------------------------------------------------------
    # Strategy: scan each line; prefer lines that have a grand-total keyword.
    # Keep ALL amounts found so the verify form can let the user pick one.
    all_amounts = []
    total_line_amount = ""

    for line in lines:
        amounts_in_line = AMOUNT_PATTERN.findall(line)
        for a in amounts_in_line:
            cleaned = a.replace(",", "").strip()
            try:
                val = float(cleaned)
                if val > 0.5:          # filter noise like "0" or "1" (page numbers etc.)
                    all_amounts.append(cleaned)
            except ValueError:
                pass

        # Strong candidate: line contains a total-keyword AND an amount
        if TOTAL_KEYWORDS.search(line) and amounts_in_line:
            # Prefer last (rightmost) amount on the line — that's the running total
            for a in reversed(amounts_in_line):
                cleaned = a.replace(",", "").strip()
                try:
                    if float(cleaned) > 0.5:
                        total_line_amount = cleaned
                        break
                except ValueError:
                    pass

    # Deduplicate while preserving insertion order
    seen = set()
    unique_amounts = []
    for a in all_amounts:
        if a not in seen:
            seen.add(a)
            unique_amounts.append(a)
    result["all_amounts"] = unique_amounts

    if total_line_amount:
        result["total_amount"] = total_line_amount
    elif unique_amounts:
        try:
            result["total_amount"] = max(unique_amounts, key=lambda x: float(x))
        except ValueError:
            pass

    # -- Vendor Name ----------------------------------------------------------
    # Strategy 1: explicit label ("From:", "Seller:", "Vendor:" etc.)
    for line in lines:
        m = VENDOR_LABEL_PATTERN.match(line)
        if m:
            candidate = m.group(1).strip()
            if len(candidate) >= 3:
                result["vendor_name"] = candidate
                break

    # Helper: lines that are mostly decorative (dashes, dots, equals, asterisks, pipes)
    def _is_decorative(line: str) -> bool:
        """Return True if 70%+ of the line's characters are non-alphabetic decoration."""
        if not line:
            return True
        alpha = sum(1 for c in line if c.isalpha())
        return alpha / len(line) < 0.3

    # Helper: detect label-only lines like "GSTIN:", "SUB-TOTAL:", "Invoice No:" etc.
    _LABEL_PREFIX = re.compile(
        r"^(?:gstin|gst\s*(?:no|number|@|18%|12%|5%|28%)?|pan|cin|"
        r"invoice|bill|tax\s+invoice|receipt|ref|voucher|"
        r"date|phone|email|address|state|place|"
        r"sub[\s\-]?total|grand\s+total|total|net\s+(?:total|amount)|"
        r"amount|discount|freight|shipping|paid|payment|balance|"
        r"item|description|qty|quantity|unit|rate|price|hsn|sac)[:\s#]",
        re.IGNORECASE,
    )

    # Strategy 2: top-of-page company header like "ABCD RETAIL LIMITED"
    if not result["vendor_name"]:
        for line in lines[:6]:
            if _looks_like_company_name(line):
                result["vendor_name"] = _clean_company_candidate(line)
                break

    # Strategy 3: text immediately preceding the first GSTIN occurrence
    # IMPORTANT: search in raw_text_upper because result["gstin"] is already
    # uppercased — raw_text.find() would return -1 if OCR lowercased the GSTIN.
    if not result["vendor_name"] and result["gstin"]:
        gstin_pos = raw_text_upper.find(result["gstin"])
        if gstin_pos > 0:
            preceding = raw_text[:gstin_pos]
            pre_lines = [l.strip() for l in preceding.splitlines() if l.strip()]
            _S2_SKIP = {
                "invoice", "bill", "receipt", "tax invoice", "purchase",
                "statement", "gstin", "gst", "cin", "pan", "items",
            }
            for pl in reversed(pre_lines[-5:]):
                if (
                    len(pl) >= 3
                    and not re.match(r"^\d", pl)
                    # .search (not .match) — reject any line that contains an amount
                    # anywhere, e.g. "SUB-TOTAL : XT 6,250.00"
                    and not AMOUNT_PATTERN.search(pl)
                    and pl.lower().rstrip(":") not in _S2_SKIP
                    # Skip label lines like "GSTIN:", "PAN:", "Invoice No:"
                    and not _LABEL_PREFIX.match(pl)
                    # Skip lines that ARE a GSTIN value (uppercase check catches lowercase OCR)
                    and not GSTIN_PATTERN.search(pl.upper())
                    # Skip decorative separator lines (dashes, dots, equals, pipes)
                    and not _is_decorative(pl)
                ):
                    result["vendor_name"] = pl
                    break

    # Strategy 4: first meaningful non-numeric line on the page
    if not result["vendor_name"]:
        _S3_SKIP = {
            "invoice", "bill", "receipt", "tax invoice", "purchase",
            "statement", "original", "duplicate", "original for recipient",
            "gstin", "gst no", "gst number", "items", "sub-total",
            "grand total", "paid by", "total",
        }
        for line in lines[:8]:
            if (
                # Check uppercase version so we catch lowercase OCR GSTIN output
                GSTIN_PATTERN.search(line.upper())
                or re.match(r"^\d", line)
                or len(line) < 3
                or line.lower().rstrip(":") in _S3_SKIP
                # Skip label lines like "GSTIN:", "SUB-TOTAL:", "Invoice No:"
                or _LABEL_PREFIX.match(line)
                # Reject any line that contains an amount anywhere (catches
                # "SUB-TOTAL : XT 6,250.00" where ₹ is misread as "XT")
                or AMOUNT_PATTERN.search(line)
                # Skip decorative separator lines (dashes, dots, equals, pipes)
                or _is_decorative(line)
            ):
                continue
            result["vendor_name"] = _clean_company_candidate(line)
            break

    # -- Confidence scoring ---------------------------------------------------
    # Each of the 4 key fields (vendor, gstin, date, amount) contributes 25 pts
    score = 0
    if result["vendor_name"]:   score += 25
    if result["gstin"]:         score += 25
    if result["date"]:          score += 25
    if result["total_amount"]:  score += 25
    result["confidence_score"] = score
    result["low_confidence"]   = score < 70

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DUPLICATE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def _amounts_within_tolerance(a: str, b: str, tolerance: float = 0.02) -> bool:
    """Return True if two amount strings are within ±2% of each other."""
    try:
        fa, fb = float(a), float(b)
        if fa == 0 and fb == 0:
            return True
        if fa == 0 or fb == 0:
            return False
        return abs(fa - fb) / max(fa, fb) <= tolerance
    except (ValueError, TypeError):
        return False


def _normalise_vendor(name: str) -> str:
    """Lowercase + strip common legal suffixes for fuzzy comparison."""
    if not name:
        return ""
    suffixes = r"\b(?:pvt|ltd|limited|llp|inc|corp|private|public|co)\b\.?"
    cleaned = re.sub(suffixes, "", name.lower(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def check_duplicate(
    company,
    gstin: str,
    total_amount: str,
    date: str,
    vendor_name: str = "",
    bill_number: str = "",
    exclude_pk: Optional[int] = None,
) -> dict:
    """
    Check if a similar bill has already been submitted.

    Detection rules (any match triggers duplicate):
      1. Same GSTIN + same bill_number
      2. Same GSTIN + amount within ±2% tolerance + same date
      3. Normalised vendor name match + bill_number match (no GSTIN)

    Returns:
        {
            "is_duplicate": bool,
            "message":      str,
            "submission_id": int | None,
        }
    """
    from .models import OCRSubmission

    # Need at least some signal to compare
    if not gstin and not bill_number and not vendor_name:
        return {"is_duplicate": False, "message": "", "submission_id": None}

    qs = OCRSubmission.objects.filter(
        company=company,
        status__in=[OCRSubmission.STATUS_CONFIRMED, OCRSubmission.STATUS_PENDING],
    ).exclude(parsed_json={})

    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)

    norm_vendor = _normalise_vendor(vendor_name)

    for sub in qs:
        pj = sub.parsed_json or {}
        sub_gstin       = pj.get("gstin", "")
        sub_amount      = pj.get("total_amount", "")
        sub_date        = pj.get("date", "")
        sub_bill_number = pj.get("bill_number", "")
        sub_vendor      = _normalise_vendor(pj.get("vendor_name", ""))

        # Rule 1: same GSTIN + same bill number
        if (
            gstin and sub_gstin and gstin == sub_gstin
            and bill_number and sub_bill_number
            and bill_number.upper() == sub_bill_number.upper()
        ):
            return {
                "is_duplicate": True,
                "message": (
                    f"Possible duplicate — same GSTIN ({gstin}) and bill number "
                    f"({bill_number}) found in Submission #{sub.pk} "
                    f"[{sub.status}]."
                ),
                "submission_id": sub.pk,
            }

        # Rule 2: same GSTIN + amount within ±2% + same date
        if (
            gstin and sub_gstin and gstin == sub_gstin
            and total_amount and sub_amount
            and _amounts_within_tolerance(total_amount, sub_amount)
            and date and sub_date and date == sub_date
        ):
            return {
                "is_duplicate": True,
                "message": (
                    f"Possible duplicate — same GSTIN ({gstin}), similar amount "
                    f"and same date found in Submission #{sub.pk} [{sub.status}]."
                ),
                "submission_id": sub.pk,
            }

        # Rule 3: matching vendor name + matching bill number (no GSTIN available)
        if (
            not gstin
            and norm_vendor and sub_vendor and norm_vendor == sub_vendor
            and bill_number and sub_bill_number
            and bill_number.upper() == sub_bill_number.upper()
        ):
            return {
                "is_duplicate": True,
                "message": (
                    f"Possible duplicate — same vendor ({vendor_name!r}) and bill "
                    f"number ({bill_number}) found in Submission #{sub.pk} "
                    f"[{sub.status}]."
                ),
                "submission_id": sub.pk,
            }

        # Rule 4 (fallback): same vendor name + amount within ±2% tolerance
        # Catches cases where OCR fails to extract GSTIN or bill number but the
        # vendor + amount combo is a strong enough signal.
        if (
            norm_vendor and sub_vendor and norm_vendor == sub_vendor
            and total_amount and sub_amount
            and _amounts_within_tolerance(total_amount, sub_amount)
        ):
            return {
                "is_duplicate": True,
                "message": (
                    f"Possible duplicate — same vendor ({vendor_name!r}) with a "
                    f"similar amount (₹{total_amount}) already exists in "
                    f"Submission #{sub.pk} [{sub.status}]."
                ),
                "submission_id": sub.pk,
            }

    return {"is_duplicate": False, "message": "", "submission_id": None}


# ─────────────────────────────────────────────────────────────────────────────
# VENDOR LEDGER AUTO-MATCH
# ─────────────────────────────────────────────────────────────────────────────

def find_vendor_ledger(company, vendor_name: str):
    """
    Try to find an existing Expense/Liability ledger matching the vendor name.

    Returns:
        (ledger_obj, confidence: str)   confidence = "exact" | "partial" | None
    """
    from ledger.models import Ledger

    if not vendor_name:
        return None, None

    # Exact match (case-insensitive)
    exact = Ledger.objects.filter(
        company=company,
        name__iexact=vendor_name,
        is_active=True,
    ).first()
    if exact:
        return exact, "exact"

    # Partial match: vendor name is contained in ledger name or vice versa
    vendor_words = [w for w in vendor_name.lower().split() if len(w) > 2]
    for word in vendor_words:
        partial = Ledger.objects.filter(
            company=company,
            name__icontains=word,
            is_active=True,
        ).first()
        if partial:
            return partial, "partial"

    return None, None


# ─────────────────────────────────────────────────────────────────────────────
# FULL PIPELINE
# ─────────────────────────────────────────────────────────────────────────────

def process_submission(submission) -> None:
    """
    Main entry point called after a file is uploaded.
    Mutates the OCRSubmission object and saves it.

    Steps:
        1. OCR the file
        2. Parse fields
        3. Vendor ledger auto-match
        4. Duplicate detection
        5. Save results to submission.parsed_json + submission.extracted_text
    """
    file_path = submission.file.path

    # 1. Run OCR
    raw_text, error = extract_text_from_file(file_path)
    submission.extracted_text = raw_text
    submission.ocr_error = error or ""

    parsed = {}
    if raw_text:
        # 2. Parse fields
        parsed = parse_fields(raw_text)

        # 3. Vendor auto-match
        vendor_ledger, confidence = find_vendor_ledger(
            submission.company,
            parsed.get("vendor_name", ""),
        )
        if vendor_ledger:
            parsed["vendor_ledger_id"] = vendor_ledger.pk
            parsed["vendor_ledger_name"] = vendor_ledger.name
            parsed["vendor_match_confidence"] = confidence
        else:
            parsed["vendor_ledger_id"] = None
            parsed["vendor_ledger_name"] = ""
            parsed["vendor_match_confidence"] = None

        # 4. Duplicate check (enhanced: vendor + bill_number + ±2% amount)
        dup = check_duplicate(
            submission.company,
            gstin=parsed.get("gstin", ""),
            total_amount=parsed.get("total_amount", ""),
            date=parsed.get("date", ""),
            vendor_name=parsed.get("vendor_name", ""),
            bill_number=parsed.get("bill_number", ""),
            exclude_pk=submission.pk,
        )
        parsed["duplicate_warning"]      = dup["message"]
        parsed["duplicate_submission_id"] = dup.get("submission_id")

        # 5. Extract line items for inventory matching
        line_items = extract_line_items(raw_text)
        parsed["line_items"] = line_items

    submission.parsed_json = parsed

    # Store raw extracted items in dedicated field
    try:
        from .services import match_line_items_to_stock
        enriched = match_line_items_to_stock(submission.company, list(parsed.get("line_items", [])))
    except Exception:
        enriched = parsed.get("line_items", [])

    submission.extracted_items = parsed.get("line_items", [])
    submission.matched_items   = enriched

    # Save duplicate_of FK when a duplicate is detected
    dup_pk = parsed.get("duplicate_submission_id")
    if dup_pk and dup_pk != submission.pk:
        from .models import OCRSubmission as _Sub
        try:
            submission.duplicate_of = _Sub.objects.get(pk=dup_pk)
        except _Sub.DoesNotExist:
            submission.duplicate_of = None
    else:
        submission.duplicate_of = None

    submission.save(update_fields=[
        "extracted_text", "parsed_json", "ocr_error",
        "extracted_items", "matched_items", "duplicate_of", "updated_at",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# LINE ITEM EXTRACTION  (Phase 4.1 — Inventory)
# ─────────────────────────────────────────────────────────────────────────────

# Regex: matches an invoice line item row — extended to capture optional HSN code.
# Handles common layouts like:
#   "Basmati Rice 5kg   1001   10   250.00   2500.00"
#   "1. Widget A (Blue)  Pcs  3  99.00  297.00"
#   "Rice         1006  5  Kgs  120.00  600.00  18%"
_LINE_ITEM_PATTERN = re.compile(
    r"^(?:\d+[.)\s]+)?"                               # optional row number
    r"(?P<name>[A-Za-z][\w\s/\-.,&+()\%]{2,60}?)"    # item name
    r"(?:\s+(?P<hsn>\d{4,8}))?"                       # optional HSN/SAC code (4-8 digits)
    r"\s+(?P<qty>\d{1,6}(?:\.\d{1,3})?)"              # quantity
    r"\s+(?:[A-Za-z]{2,8}\s+)?"                       # optional unit label
    r"(?P<rate>\d{1,6}(?:,\d{2,3})*(?:\.\d{1,2})?)"  # unit rate
    r"(?:\s+(?P<amount>\d{1,8}(?:,\d{2,3})*(?:\.\d{1,2})?))?",   # optional line total
    re.IGNORECASE,
)

# Tax rate on same line: "18%" or "18.00%" or "IGST 18"
_TAX_ON_LINE_PATTERN = re.compile(
    r"(?:CGST|SGST|IGST|GST|TAX)?\s*(\d{1,2}(?:\.\d{1,2})?)\s*%",
    re.IGNORECASE,
)

# Standalone HSN row pattern (some invoices put HSN on its own line below item)
_HSN_ROW_PATTERN = re.compile(r"^HSN\s*[:/]?\s*(\d{4,8})\b", re.IGNORECASE)

# Lines to skip — headers, tax rows, total rows
_SKIP_LINE_KEYWORDS = re.compile(
    r"^(?:total|sub.?total|grand\s+total|net\s+amount|amount\s+due|"
    r"discount|freight|shipping|cgst|sgst|igst|gst|tax|"
    r"cess|round\s+off|advance|balance|paid|debit|credit|"
    r"qty|quantity|unit|rate|price|amount|s\.no|sr\.no|hsn|sac"
    r"|invoice\s+no|bill\s+no|description|particulars|item\s+name)",
    re.IGNORECASE,
)


def _extract_tax_from_line(line: str) -> str:
    """Extract a tax percentage from a line string. Returns '' if not found."""
    m = _TAX_ON_LINE_PATTERN.search(line)
    return m.group(1) if m else ""


def extract_line_items(raw_text: str) -> list:
    """
    Extract individual line items from raw OCR text.

    Returns a list of dicts (up to 30 items):
        [
            {
              "name":      "Basmati Rice",
              "quantity":  "10",
              "rate":      "250.00",
              "amount":    "2500.00",
              "hsn":       "1006",          # blank if not found
              "tax_rate":  "5",             # % string, blank if not found
            },
            ...
        ]

    Best-effort heuristic extraction.  The OCR verify step lets the user
    accept, edit, or discard each extracted line before creating a voucher.
    """
    items = []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    in_items_section = False
    last_item_index  = -1  # track index of last appended item for HSN back-fill

    for line in lines:
        line_upper = line.upper()

        # Detect the table header row — activates item extraction mode
        if not in_items_section and re.search(
            r"(?:ITEM|DESCRIPTION|PARTICULARS|PRODUCT).*(?:QTY|QUANTITY).*(?:RATE|PRICE)",
            line_upper,
        ):
            in_items_section = True
            continue

        # Stop at totals section
        if TOTAL_KEYWORDS.search(line):
            break

        # Check for standalone HSN row and back-fill into last item
        hsn_row_m = _HSN_ROW_PATTERN.match(line)
        if hsn_row_m and last_item_index >= 0:
            if not items[last_item_index].get("hsn"):
                items[last_item_index]["hsn"] = hsn_row_m.group(1)
            continue

        # Skip known non-item lines
        if _SKIP_LINE_KEYWORDS.search(line):
            continue

        # Try matching line item pattern
        m = _LINE_ITEM_PATTERN.match(line + " ")
        if not m:
            continue

        name     = m.group("name").strip().rstrip(".,")
        hsn      = (m.group("hsn") or "").strip()
        qty_str  = m.group("qty").replace(",", "")
        rate_str = m.group("rate").replace(",", "")
        amt_raw  = m.group("amount") or ""
        amt_str  = amt_raw.replace(",", "")
        tax_str  = _extract_tax_from_line(line)

        # Sanity checks
        try:
            qty  = float(qty_str)
            rate = float(rate_str)
        except ValueError:
            continue

        if rate <= 0 or qty <= 0 or qty > 100_000 or rate > 10_000_000:
            continue
        if len(name) < 3 or name.replace(" ", "").isdigit():
            continue

        items.append({
            "name":     name,
            "quantity": qty_str,
            "rate":     rate_str,
            "amount":   amt_str if amt_str else f"{qty * rate:.2f}",
            "hsn":      hsn,
            "tax_rate": tax_str,
        })
        last_item_index = len(items) - 1

    # De-duplicate by normalised item name (keep first occurrence)
    seen_names: set = set()
    unique_items = []
    for item in items:
        norm = item["name"].lower().strip()
        if norm not in seen_names:
            seen_names.add(norm)
            unique_items.append(item)

    return unique_items[:30]


# ─────────────────────────────────────────────────────────────────────────────
# INVENTORY ITEM MATCHING  (Phase 4.1)
# ─────────────────────────────────────────────────────────────────────────────

def match_line_items_to_stock(company, line_items: list) -> list:
    """
    Try to match each extracted line item name to an existing StockItem for the
    given company.

    Returns the same list with extra keys per entry:
        "matched_item_id"   — StockItem.pk if matched, else None
        "matched_item_name" — StockItem.name if matched, else ""
        "match_confidence"  — "exact" | "partial" | "none"

    Matching rules (applied in order):
      1. Exact case-insensitive name match → "exact"
      2. Extracted name is a substring of an existing item name (or vice versa) → "partial"
      3. No match → "none"
    """
    try:
        from inventory.models import StockItem
    except ImportError:
        # Inventory app not installed — return items unmatched
        for item in line_items:
            item.update({"matched_item_id": None, "matched_item_name": "", "match_confidence": "none"})
        return line_items

    stock_items = list(
        StockItem.objects.filter(company=company, is_active=True).values("id", "name")
    )

    for item in line_items:
        extracted_name = item.get("name", "").lower().strip()
        best_id   = None
        best_name = ""
        best_conf = "none"

        for si in stock_items:
            si_name_lower = si["name"].lower().strip()
            if extracted_name == si_name_lower:
                best_id   = si["id"]
                best_name = si["name"]
                best_conf = "exact"
                break
            if (
                best_conf != "exact"
                and len(extracted_name) >= 4
                and (extracted_name in si_name_lower or si_name_lower in extracted_name)
            ):
                best_id   = si["id"]
                best_name = si["name"]
                best_conf = "partial"

        item["matched_item_id"]   = best_id
        item["matched_item_name"] = best_name
        item["match_confidence"]  = best_conf

    return line_items
