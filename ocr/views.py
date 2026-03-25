"""
ocr/views.py

View flow:
  1. ocr_upload            → user picks file → OCR task → redirect to verify
  2. ocr_status            → AJAX polling (JSON status)
  3. ocr_verify            → side-by-side preview + editable form + line items table
                             POST saves form data + confirmed_items → redirect confirm
  4. ocr_confirm           → creates Purchase Voucher + VoucherStockItems + StockLedger
                             atomically
  5. ocr_list              → list all submissions
  6. ocr_reject            → mark rejected
  7. stock_item_quick_create → AJAX: create StockItem on-the-fly from line-items table
"""

import json
import logging
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.decorators import write_required
from ledger.models import Ledger
from vouchers.models import Voucher, VoucherItem
from .models import OCRSubmission
from .forms import OCRUploadForm, OCRVerifyForm
from . import ocr_utils

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Upload — async dispatch
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def ocr_upload(request):
    company = request.current_company
    form    = OCRUploadForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        submission = form.save(commit=False)
        submission.company = company
        submission.status  = OCRSubmission.STATUS_PROCESSING
        submission.save()

        try:
            from .tasks import process_ocr_submission
            result = process_ocr_submission.delay(submission.pk)
            submission.task_id = result.id or ""
            submission.save(update_fields=["task_id"])
        except Exception as exc:
            logger.warning("Celery unavailable (%s); running OCR synchronously.", exc)
            try:
                ocr_utils.process_submission(submission)
                submission.status = OCRSubmission.STATUS_PENDING
            except Exception as ocr_exc:
                logger.exception("Sync OCR failed for submission %s", submission.pk)
                submission.ocr_error = str(ocr_exc)
                submission.status    = OCRSubmission.STATUS_ERROR
            submission.save(update_fields=["status", "ocr_error", "updated_at"])

        if submission.parsed_json.get("duplicate_warning"):
            messages.warning(request, submission.parsed_json["duplicate_warning"])

        return redirect("ocr:verify", pk=submission.pk)

    return render(request, "ocr/ocr_upload.html", {"form": form})


# ─────────────────────────────────────────────────────────────────────────────
# 2. Status — AJAX polling
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ocr_status(request, pk):
    company    = request.current_company
    submission = get_object_or_404(OCRSubmission, pk=pk, company=company)
    return JsonResponse({
        "status":   submission.status,
        "is_ready": submission.status == OCRSubmission.STATUS_PENDING,
        "is_error": submission.status == OCRSubmission.STATUS_ERROR,
        "error":    submission.ocr_error or "",
    })


# ─────────────────────────────────────────────────────────────────────────────
# 3. Verify — side-by-side preview + editable form + line items
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def ocr_verify(request, pk):
    company    = request.current_company
    submission = get_object_or_404(OCRSubmission, pk=pk, company=company)

    if submission.status == OCRSubmission.STATUS_CONFIRMED:
        messages.info(request, "This submission has already been confirmed.")
        return redirect("ocr:list")

    pj = submission.parsed_json or {}

    if request.method == "POST":
        form = OCRVerifyForm(request.POST, company=company, initial_parsed=pj)
        if form.is_valid():
            cd = form.cleaned_data

            # Parse line items submitted from the table
            confirmed_items = _parse_line_items_from_post(request.POST)

            submission.parsed_json.update({
                "vendor_name":       cd["vendor_name"],
                "gstin":             cd.get("gstin", ""),
                "date":              cd["date"].strftime("%Y-%m-%d"),
                "total_amount":      str(cd["total_amount"]),
                "expense_ledger_id": cd["expense_ledger"].pk,
                "payment_ledger_id": cd["payment_ledger"].pk,
                "narration":         cd.get("narration", ""),
                "confirmed_items":   confirmed_items,
            })
            submission.matched_items = confirmed_items
            submission.save(update_fields=["parsed_json", "matched_items", "updated_at"])
            return redirect("ocr:confirm", pk=submission.pk)
    else:
        form = OCRVerifyForm(company=company, initial_parsed=pj)

    # Build matched line items for the UI
    matched_items = _get_matched_items(submission, company)

    # All active stock items for dropdown/autocomplete
    try:
        from inventory.models import StockItem
        stock_items_qs = StockItem.objects.filter(
            company=company, is_active=True
        ).select_related("hsn_sac", "tax_rate").order_by("name")
        stock_items_json = json.dumps([
            {
                "id":           s.pk,
                "name":         s.name,
                "unit":         s.unit,
                "purchase_price": str(s.purchase_price),
                "selling_price":  str(s.selling_price),
                "hsn_code":     s.hsn_sac.code if s.hsn_sac else "",
                "tax_rate_pct": str(s.tax_rate.rate) if s.tax_rate else "",
            }
            for s in stock_items_qs
        ])
    except Exception:
        stock_items_qs   = []
        stock_items_json = "[]"

    context = {
        "submission":              submission,
        "form":                    form,
        "pj":                      pj,
        "all_amounts":             pj.get("all_amounts", []),
        "raw_lines":               pj.get("raw_lines", []),
        "vendor_match_confidence": pj.get("vendor_match_confidence"),
        "vendor_ledger_name":      pj.get("vendor_ledger_name", ""),
        "duplicate_warning":       pj.get("duplicate_warning", ""),
        "matched_items":           matched_items,
        "stock_items_json":        stock_items_json,
        "is_processing":           submission.is_processing(),
        "is_error":                submission.status == OCRSubmission.STATUS_ERROR,
        "ocr_error":               submission.ocr_error,
    }
    return render(request, "ocr/ocr_verify.html", context)


def _get_matched_items(submission, company):
    """Return matched items — re-run matching if not stored yet."""
    items = submission.matched_items
    if items:
        return items
    raw = submission.extracted_items or submission.parsed_json.get("line_items", [])
    if not raw:
        return []
    try:
        from .services import match_line_items_to_stock
        return match_line_items_to_stock(company, list(raw))
    except Exception:
        return raw


def _parse_line_items_from_post(post_data) -> list:
    """
    Parse line item fields from POST.
    Expected keys: item_count, items-N-stock_item_id, items-N-name,
                   items-N-qty, items-N-rate, items-N-hsn, items-N-tax_rate
    """
    try:
        count = int(post_data.get("item_count", 0))
    except (ValueError, TypeError):
        return []

    items = []
    for i in range(count):
        prefix = f"items-{i}-"
        name = post_data.get(f"{prefix}name", "").strip()
        if not name:
            continue
        sid_raw = post_data.get(f"{prefix}stock_item_id", "").strip()
        try:
            sid = int(sid_raw) if sid_raw else 0
        except ValueError:
            sid = 0
        items.append({
            "stock_item_id": sid,
            "name":          name,
            "quantity":      post_data.get(f"{prefix}qty",      "0").strip(),
            "rate":          post_data.get(f"{prefix}rate",     "0").strip(),
            "hsn":           post_data.get(f"{prefix}hsn",      "").strip(),
            "tax_rate":      post_data.get(f"{prefix}tax_rate", "").strip(),
        })
    return items


# ─────────────────────────────────────────────────────────────────────────────
# 4. Confirm → create Purchase Voucher + stock movements atomically
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def ocr_confirm(request, pk):
    company    = request.current_company
    submission = get_object_or_404(OCRSubmission, pk=pk, company=company)

    if submission.status == OCRSubmission.STATUS_CONFIRMED:
        messages.info(request, "This submission is already confirmed.")
        return redirect("ocr:list")

    pj = submission.parsed_json or {}

    # ── Resolve ledgers ──
    try:
        expense_ledger = Ledger.objects.get(pk=pj["expense_ledger_id"], company=company)
        payment_ledger = Ledger.objects.get(pk=pj["payment_ledger_id"], company=company)
    except (Ledger.DoesNotExist, KeyError):
        messages.error(
            request,
            "Could not resolve ledger accounts. Please go back and select them manually.",
        )
        return redirect("ocr:verify", pk=submission.pk)

    # ── Resolve amount ──
    try:
        amount = Decimal(str(pj["total_amount"]))
        if amount <= 0:
            raise ValueError("Amount must be positive.")
    except (InvalidOperation, ValueError, KeyError) as exc:
        messages.error(request, f"Invalid amount: {exc}")
        return redirect("ocr:verify", pk=submission.pk)

    # ── Resolve date ──
    try:
        from datetime import datetime
        bill_date = datetime.strptime(pj["date"], "%Y-%m-%d").date()
    except (KeyError, ValueError):
        bill_date = timezone.now().date()

    narration = pj.get(
        "narration",
        f"Purchase bill from {pj.get('vendor_name', 'Unknown')} — "
        f"OCR Submission #{submission.pk}",
    )

    # ── Build stock lines from confirmed items ──
    confirmed_items = pj.get("confirmed_items") or submission.matched_items or []
    stock_lines = []
    try:
        from .services import build_stock_lines_from_confirmed
        stock_lines = build_stock_lines_from_confirmed(company, confirmed_items)
    except Exception as exc:
        logger.warning("Could not build stock lines: %s", exc)

    # ── Atomic creation ──
    with transaction.atomic():
        voucher = Voucher.objects.create(
            company=company,
            date=bill_date,
            voucher_type="Purchase",
            narration=narration,
        )

        # Accounting double-entry
        VoucherItem.objects.create(
            voucher=voucher, ledger=expense_ledger,
            debit=amount, credit=Decimal("0.00"),
            narration=f"Purchase: {pj.get('vendor_name', '')}",
        )
        VoucherItem.objects.create(
            voucher=voucher, ledger=payment_ledger,
            debit=Decimal("0.00"), credit=amount,
            narration=f"Payable to: {pj.get('vendor_name', '')}",
        )

        # Inventory: VoucherStockItems + StockLedger
        if stock_lines:
            from inventory.models import VoucherStockItem
            from inventory.stock_utils import process_purchase_stock

            for line in stock_lines:
                VoucherStockItem.objects.create(
                    voucher=voucher,
                    stock_item=line["stock_item"],
                    quantity=line["quantity"],
                    rate=line["rate"],
                )

            process_purchase_stock(voucher, stock_lines, user=request.user)

        submission.linked_voucher = voucher
        submission.status         = OCRSubmission.STATUS_CONFIRMED
        submission.save(update_fields=["linked_voucher", "status", "updated_at"])

    stock_note = f" with {len(stock_lines)} stock line(s)" if stock_lines else ""
    messages.success(
        request,
        f"✅ Purchase Voucher {voucher.number} created{stock_note} from OCR bill!",
    )
    return redirect("vouchers:detail", pk=voucher.pk)


# ─────────────────────────────────────────────────────────────────────────────
# 5. List
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def ocr_list(request):
    company     = request.current_company
    submissions = OCRSubmission.objects.filter(company=company).order_by("-created_at")
    return render(request, "ocr/ocr_list.html", {"submissions": submissions})


# ─────────────────────────────────────────────────────────────────────────────
# 6. Reject
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def ocr_reject(request, pk):
    company    = request.current_company
    submission = get_object_or_404(OCRSubmission, pk=pk, company=company)

    if request.method == "POST":
        submission.status = OCRSubmission.STATUS_REJECTED
        submission.save(update_fields=["status", "updated_at"])
        messages.warning(request, f"Submission #{submission.pk} marked as rejected.")
        return redirect("ocr:list")

    return render(request, "ocr/ocr_reject_confirm.html", {"submission": submission})


# ─────────────────────────────────────────────────────────────────────────────
# 7. AJAX — quick-create StockItem from line items table
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
@require_POST
def stock_item_quick_create(request):
    """
    AJAX endpoint — create a StockItem on-the-fly from the OCR verify page.

    Accepts JSON body:
        {"name", "unit", "hsn_code", "tax_rate_pct", "purchase_price",
         "selling_price", "opening_quantity"}

    Returns JSON:
        {"success": true, "id": pk, "name": ..., "unit": ...,
         "purchase_price": ..., "hsn_code": ..., "tax_rate_pct": ...}
    """
    company = request.current_company
    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        data = request.POST.dict()

    try:
        from .services import quick_create_stock_item
        stock_item, created = quick_create_stock_item(company, data, user=request.user)
    except ValueError as exc:
        return JsonResponse({"success": False, "error": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("stock_item_quick_create failed")
        return JsonResponse({"success": False, "error": "Server error."}, status=500)

    return JsonResponse({
        "success":        True,
        "created":        created,
        "id":             stock_item.pk,
        "name":           stock_item.name,
        "unit":           stock_item.unit,
        "purchase_price": str(stock_item.purchase_price),
        "selling_price":  str(stock_item.selling_price),
        "hsn_code":       stock_item.hsn_sac.code if stock_item.hsn_sac else "",
        "tax_rate_pct":   str(stock_item.tax_rate.rate) if stock_item.tax_rate else "",
    })
