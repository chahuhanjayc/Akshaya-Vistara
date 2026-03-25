"""
vouchers/views.py

Voucher CRUD + bulk actions + advanced filtering + pagination + simulate payment.

Phase 4.1 addition:
  - voucher_create and voucher_edit now also accept an optional
    VoucherStockItemFormSet.  When Sales or Purchase vouchers are saved:
      • VoucherStockItem rows are saved (inline to the Voucher).
      • Old StockLedger entries for this voucher are deleted and recreated so
        edits are idempotent.
    All of this happens inside the same transaction.atomic() block that saves
    the Voucher and VoucherItems — stock and accounting stay in sync.

Access control:
  list / detail         → all authenticated users with company access
  create / edit         → Admin, Accountant
  delete                → Admin only
  bulk_delete           → Admin only
  bulk_export_pdf       → all roles (read-only export)
  simulate_payment      → Admin, Accountant
"""

from datetime import date as _date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404

from core.decorators import admin_required, write_required
from core.models import AuditLog
from .models import Voucher, VoucherItem
from .forms import VoucherForm, VoucherItemFormSet
from .utils import generate_upi_qr

PAGE_SIZE = 25


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _make_formset(company, *args, **kwargs):
    """Return a VoucherItemFormSet with ledger choices scoped to the company."""
    fs = VoucherItemFormSet(*args, **kwargs)
    for form in fs.forms:
        form.fields["ledger"].queryset = (
            __import__("ledger.models", fromlist=["Ledger"])
            .Ledger.objects.filter(company=company, is_active=True)
            .order_by("group", "name")
        )
    return fs


def _make_stock_formset(company, *args, **kwargs):
    """
    Return a VoucherStockItemFormSet with stock item choices scoped to the company.
    Imported lazily to avoid circular-import issues at module load time.
    """
    from inventory.forms import VoucherStockItemFormSet
    from inventory.models import StockItem

    fs = VoucherStockItemFormSet(*args, **kwargs)
    for form in fs.forms:
        form.fields["stock_item"].queryset = StockItem.objects.filter(
            company=company, is_active=True
        ).order_by("name")
    return fs


def _check_balance(formset):
    """Validate double-entry balance. Returns (valid_forms, dr, cr, error|None)."""
    valid_forms = [
        f for f in formset
        if f.cleaned_data and not f.cleaned_data.get("DELETE", False)
    ]
    dr = cr = Decimal("0.00")
    for f in valid_forms:
        dr += f.cleaned_data.get("debit",  Decimal("0.00"))
        cr += f.cleaned_data.get("credit", Decimal("0.00"))

    if not valid_forms:
        return valid_forms, dr, cr, "Please add at least one voucher line."
    if dr != cr:
        diff = abs(dr - cr)
        return valid_forms, dr, cr, (
            f"Voucher is NOT balanced! "
            f"Total Debit = ₹{dr:.2f}, Total Credit = ₹{cr:.2f}. "
            f"Difference = ₹{diff:.2f}. Please fix before saving."
        )
    return valid_forms, dr, cr, None


def _save_stock_movements(voucher, stock_formset):
    """
    Called inside transaction.atomic().
    1. Delete all existing StockLedger entries for this voucher (idempotent edits).
    2. Save non-empty VoucherStockItem rows.
    3. Create a StockLedger entry for each row:
         Purchase → positive qty (inward)
         Sales    → negative qty (outward)
    """
    from inventory.models import StockLedger

    # 1. Clear old stock movements for this voucher
    StockLedger.objects.filter(voucher=voucher).delete()

    # 2. Save the formset rows
    stock_formset.instance = voucher
    stock_formset.save()

    # 3. Create StockLedger entries for each saved row
    is_purchase = voucher.voucher_type == "Purchase"
    is_sales    = voucher.voucher_type == "Sales"

    if not (is_purchase or is_sales):
        return  # Only track stock for Sales and Purchase vouchers

    for vsi in voucher.voucher_stock_items.all():
        if vsi.quantity <= 0:
            continue
        # Positive qty = inward (Purchase), Negative = outward (Sales)
        qty = vsi.quantity if is_purchase else -vsi.quantity
        StockLedger.objects.create(
            stock_item=vsi.stock_item,
            voucher=voucher,
            date=voucher.date,
            quantity=qty,
            rate=vsi.rate,
        )


def _parse_filters(request):
    """Extract and sanitise GET filter params. Returns (filters_dict, filter_qs_str)."""
    filters = {
        "q":            request.GET.get("q", "").strip(),
        "start_date":   request.GET.get("start_date", "").strip(),
        "end_date":     request.GET.get("end_date", "").strip(),
        "voucher_type": request.GET.get("voucher_type", "").strip(),
        "ledger":       request.GET.get("ledger", "").strip(),
    }
    # Build a query-string fragment for pagination links (?page=N&q=...&...)
    active = {k: v for k, v in filters.items() if v}
    filter_qs = ("&" + urlencode(active)) if active else ""
    return filters, filter_qs


def _apply_filters(qs, filters):
    """Apply the filter dict to a Voucher queryset."""
    q            = filters["q"]
    start_date   = filters["start_date"]
    end_date     = filters["end_date"]
    voucher_type = filters["voucher_type"]
    ledger       = filters["ledger"]

    if q:
        qs = qs.filter(Q(number__icontains=q) | Q(narration__icontains=q))
    if start_date:
        try:
            qs = qs.filter(date__gte=_date.fromisoformat(start_date))
        except ValueError:
            pass
    if end_date:
        try:
            qs = qs.filter(date__lte=_date.fromisoformat(end_date))
        except ValueError:
            pass
    if voucher_type:
        qs = qs.filter(voucher_type=voucher_type)
    if ledger:
        qs = qs.filter(items__ledger__name__icontains=ledger).distinct()

    return qs


# ─────────────────────────────────────────────────────────────────────────────
# LIST VIEW (with filters + pagination)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def voucher_list(request):
    company  = request.current_company
    filters, filter_qs = _parse_filters(request)

    qs = (
        Voucher.objects.filter(company=company)
        .prefetch_related("items__ledger")
        .order_by("-date", "-created_at")
    )
    qs = _apply_filters(qs, filters)

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj  = paginator.get_page(request.GET.get("page"))

    # Check if any filter is actually active (for the "clear filters" link)
    has_filters = any(filters.values())

    return render(request, "vouchers/voucher_list.html", {
        "page_obj":      page_obj,
        "filters":       filters,
        "filter_qs":     filter_qs,
        "has_filters":   has_filters,
        "total_count":   qs.count(),
        "voucher_types": Voucher.VOUCHER_TYPE_CHOICES,
    })


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def voucher_create(request):
    company = request.current_company

    if request.method == "POST":
        form         = VoucherForm(request.POST)
        formset      = _make_formset(company, request.POST)
        stock_formset = _make_stock_formset(company, request.POST)

        if form.is_valid() and formset.is_valid() and stock_formset.is_valid():
            valid_forms, dr, cr, err = _check_balance(formset)
            if err:
                messages.error(request, err)
                return render(request, "vouchers/voucher_form.html", {
                    "form": form, "formset": formset,
                    "stock_formset": stock_formset,
                    "title": "New Voucher",
                    "total_dr": dr, "total_cr": cr,
                })

            with transaction.atomic():
                voucher = form.save(commit=False)
                voucher.company = company
                voucher.save()
                formset.instance = voucher
                formset.save()
                # Save stock items and create StockLedger entries
                _save_stock_movements(voucher, stock_formset)
                AuditLog.log(request, AuditLog.ACTION_CREATE, voucher)

            messages.success(request, f"Voucher {voucher.number} created successfully.")
            return redirect("vouchers:list")
    else:
        form          = VoucherForm()
        formset       = _make_formset(company)
        stock_formset = _make_stock_formset(company)

    return render(request, "vouchers/voucher_form.html", {
        "form":          form,
        "formset":       formset,
        "stock_formset": stock_formset,
        "title":         "New Voucher",
    })


@login_required
@write_required
def voucher_edit(request, pk):
    company = request.current_company
    voucher = get_object_or_404(Voucher, pk=pk, company=company)

    if request.method == "POST":
        form          = VoucherForm(request.POST, instance=voucher)
        formset       = _make_formset(company, request.POST, instance=voucher)
        stock_formset = _make_stock_formset(company, request.POST, instance=voucher)

        if form.is_valid() and formset.is_valid() and stock_formset.is_valid():
            valid_forms, dr, cr, err = _check_balance(formset)
            if err:
                messages.error(request, err)
                return render(request, "vouchers/voucher_form.html", {
                    "form": form, "formset": formset,
                    "stock_formset": stock_formset,
                    "title": "Edit Voucher", "voucher": voucher,
                    "total_dr": dr, "total_cr": cr,
                })

            with transaction.atomic():
                form.save()
                formset.save()
                # Delete old stock movements and recreate from new formset data
                _save_stock_movements(voucher, stock_formset)
                AuditLog.log(request, AuditLog.ACTION_UPDATE, voucher)

            messages.success(request, f"Voucher {voucher.number} updated.")
            return redirect("vouchers:list")
    else:
        form          = VoucherForm(instance=voucher)
        formset       = _make_formset(company, instance=voucher)
        stock_formset = _make_stock_formset(company, instance=voucher)

    return render(request, "vouchers/voucher_form.html", {
        "form":          form,
        "formset":       formset,
        "stock_formset": stock_formset,
        "title":         "Edit Voucher",
        "voucher":       voucher,
    })


@login_required
def voucher_detail(request, pk):
    company = request.current_company
    voucher = get_object_or_404(
        Voucher.objects.prefetch_related(
            "items__ledger",
            "voucher_stock_items__stock_item",
        ),
        pk=pk, company=company
    )
    audit_logs = AuditLog.objects.filter(
        company=company, model_name="Voucher", object_id=pk
    ).select_related("user")[:20]

    # Generate UPI QR only for Sales vouchers where a UPI ID is configured
    qr_code = None
    if voucher.voucher_type == "Sales":
        qr_code = generate_upi_qr(voucher)

    return render(request, "vouchers/voucher_detail.html", {
        "voucher":    voucher,
        "audit_logs": audit_logs,
        "qr_code":    qr_code,
        "today":      _date.today(),
    })


@login_required
@admin_required
def voucher_delete(request, pk):
    company = request.current_company
    voucher = get_object_or_404(Voucher, pk=pk, company=company)

    if request.method == "POST":
        num = voucher.number
        AuditLog.log(request, AuditLog.ACTION_DELETE, voucher)
        # CASCADE on StockLedger and VoucherStockItem handles inventory cleanup
        voucher.delete()
        messages.success(request, f"Voucher {num} deleted.")
        return redirect("vouchers:list")

    return render(request, "vouchers/voucher_confirm_delete.html", {"voucher": voucher})


# ─────────────────────────────────────────────────────────────────────────────
# BULK ACTIONS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def bulk_action(request):
    """
    Handles two bulk actions posted from the voucher list:
      action=delete      → Admin only, deletes the selected vouchers.
      action=export_pdf  → All roles, renders a print-optimised HTML page.
    """
    if request.method != "POST":
        return redirect("vouchers:list")

    company      = request.current_company
    action       = request.POST.get("action", "")
    selected_ids = request.POST.getlist("selected_ids")

    if not selected_ids:
        messages.warning(request, "No vouchers were selected.")
        return redirect("vouchers:list")

    # Scope to company for security — never trust client-supplied PKs alone
    vouchers = Voucher.objects.filter(pk__in=selected_ids, company=company)

    # ── Bulk Delete (Admin only) ──────────────────────────────────────────────
    if action == "delete":
        role = getattr(request, "current_company_role", None)
        if role != "Admin":
            messages.error(
                request,
                "Permission denied. Only Admins can bulk-delete vouchers."
            )
            return redirect("vouchers:list")

        count = vouchers.count()
        if count == 0:
            messages.warning(request, "No matching vouchers found.")
            return redirect("vouchers:list")

        with transaction.atomic():
            for v in vouchers:
                AuditLog.log(request, AuditLog.ACTION_DELETE, v)
            # CASCADE deletes StockLedger + VoucherStockItem rows too
            vouchers.delete()

        messages.success(request, f"{count} voucher(s) deleted successfully.")
        return redirect("vouchers:list")

    # ── Bulk Export to PDF (all roles) ────────────────────────────────────────
    if action == "export_pdf":
        vouchers = (
            vouchers
            .prefetch_related("items__ledger")
            .order_by("date", "number")
        )
        return render(request, "vouchers/bulk_print.html", {
            "vouchers":       vouchers,
            "exported_count": vouchers.count(),
        })

    messages.warning(request, "Unknown bulk action.")
    return redirect("vouchers:list")


# ─────────────────────────────────────────────────────────────────────────────
# SIMULATE PAYMENT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def simulate_payment(request, pk):
    """
    POST-only view that auto-creates a balanced Receipt voucher to simulate
    payment received against a Sales voucher.

    Logic:
      1. Load the Sales Voucher (must belong to this company).
      2. Collect all debit items (the Debtor / customer receivable accounts).
      3. Find a Bank or Cash ledger in the company (Asset group, name contains
         "bank" or "cash").
      4. Create a Receipt voucher inside an atomic transaction:
            Dr  Bank / Cash        ← total amount received
            Cr  Debtor ledger(s)   ← clearing the receivable
      5. Log the action in AuditLog.
      6. Redirect back to the Sales Voucher detail with a success message.
    """
    if request.method != "POST":
        return redirect("vouchers:detail", pk=pk)

    company = request.current_company

    # ── 1. Load the Sales Voucher ─────────────────────────────────────────────
    sales_voucher = get_object_or_404(
        Voucher.objects.prefetch_related("items__ledger"),
        pk=pk,
        company=company,
        voucher_type="Sales",
    )

    # ── 2. Collect debit items (Debtor / receivable side) ─────────────────────
    debit_items = [item for item in sales_voucher.items.all() if item.debit > 0]
    if not debit_items:
        messages.error(
            request,
            "Cannot simulate payment: the Sales Voucher has no debit (receivable) entries."
        )
        return redirect("vouchers:detail", pk=pk)

    total_amount = sum(item.debit for item in debit_items)

    # ── 3. Find Bank / Cash ledger ────────────────────────────────────────────
    from ledger.models import Ledger

    bank_ledger = (
        Ledger.objects.filter(company=company, group="Asset", is_active=True)
        .filter(
            Q(name__icontains="bank") | Q(name__icontains="cash")
        )
        .first()
    )
    if not bank_ledger:
        messages.error(
            request,
            "No active Bank or Cash ledger found for this company. "
            "Please create one (group = Asset, name containing 'Bank' or 'Cash') first."
        )
        return redirect("vouchers:detail", pk=pk)

    # ── 4. Create the Receipt Voucher ─────────────────────────────────────────
    with transaction.atomic():
        receipt = Voucher(
            company=company,
            date=_date.today(),
            voucher_type="Receipt",
            narration=f"Payment received against Sales Voucher {sales_voucher.number}",
        )
        receipt.save()  # triggers number generation

        # Dr: Bank / Cash — money received
        VoucherItem.objects.create(
            voucher=receipt,
            ledger=bank_ledger,
            debit=total_amount,
            credit=Decimal("0.00"),
        )

        # Cr: each Debtor ledger from the original — clearing the receivable
        for item in debit_items:
            VoucherItem.objects.create(
                voucher=receipt,
                ledger=item.ledger,
                debit=Decimal("0.00"),
                credit=item.debit,
            )

        AuditLog.log(
            request,
            AuditLog.ACTION_CREATE,
            receipt,
            extra={"simulated_from": sales_voucher.number},
        )

    # ── 5. Redirect with success ──────────────────────────────────────────────
    messages.success(
        request,
        f"Payment Simulated! Receipt Voucher {receipt.number} created automatically."
    )
    return redirect("vouchers:detail", pk=pk)


# ─────────────────────────────────────────────────────────────────────────────
# OUTSTANDING STATEMENT (Bill-to-Bill)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def outstanding_statement(request):
    """
    Shows every Sales and Purchase invoice for the company with:
      - Total invoice amount
      - Amount settled so far (via reference_voucher links)
      - Outstanding balance remaining
      - Days overdue (if due_date is set)

    The user can filter by type (Sales / Purchase / All) and by status
    (Outstanding / Settled / All).
    """
    company   = request.current_company
    today     = _date.today()
    type_filter   = request.GET.get("type", "Sales")
    status_filter = request.GET.get("status", "outstanding")

    # Base queryset: Sales and/or Purchase vouchers
    allowed_types = ["Sales", "Purchase"]
    if type_filter in allowed_types:
        qs = Voucher.objects.filter(company=company, voucher_type=type_filter)
    else:
        qs = Voucher.objects.filter(company=company, voucher_type__in=allowed_types)

    qs = qs.prefetch_related("items", "settlements").order_by("date", "number")

    # Annotate with financial data
    rows = []
    total_invoiced   = Decimal("0.00")
    total_settled    = Decimal("0.00")
    total_outstanding = Decimal("0.00")

    for v in qs:
        invoice_total = v.total_debit()
        settled       = v.amount_settled()
        outstanding   = v.outstanding_amount()
        fully_settled = outstanding == Decimal("0.00")

        # Days overdue: positive = overdue, negative = still within term
        days_overdue = None
        if v.due_date:
            days_overdue = (today - v.due_date).days

        rows.append({
            "voucher":       v,
            "invoice_total": invoice_total,
            "settled":       settled,
            "outstanding":   outstanding,
            "fully_settled": fully_settled,
            "days_overdue":  days_overdue,
            "is_overdue":    (days_overdue is not None and days_overdue > 0 and not fully_settled),
        })

        total_invoiced    += invoice_total
        total_settled     += settled
        total_outstanding += outstanding

    # Filter by status after annotation
    if status_filter == "outstanding":
        rows = [r for r in rows if not r["fully_settled"]]
    elif status_filter == "settled":
        rows = [r for r in rows if r["fully_settled"]]

    return render(request, "vouchers/outstanding_statement.html", {
        "rows":              rows,
        "total_invoiced":    total_invoiced,
        "total_settled":     total_settled,
        "total_outstanding": total_outstanding,
        "type_filter":       type_filter,
        "status_filter":     status_filter,
        "today":             today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# SERVER-SIDE PDF INVOICE (WeasyPrint)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def invoice_pdf(request, pk):
    """
    Generate a professional, server-side PDF invoice for any voucher using
    WeasyPrint. Produces byte-identical output on every device — no browser
    print-to-PDF variance.

    Query parameter:
        ?download=1  → Content-Disposition: attachment (forces download)
        (default)    → inline (browser renders PDF viewer)
    """
    import io
    from django.http import HttpResponse
    from django.template.loader import render_to_string
    import weasyprint

    company = request.current_company
    voucher = get_object_or_404(
        Voucher.objects.prefetch_related("items__ledger"), pk=pk, company=company
    )
    qr_code = generate_upi_qr(voucher) if voucher.voucher_type == "Sales" else None

    # Render standalone HTML template to a string
    html_str = render_to_string("vouchers/invoice_pdf.html", {
        "voucher": voucher,
        "company": company,
        "qr_code": qr_code,
        "today":   _date.today(),
    }, request=request)

    # Convert to PDF
    pdf_bytes = weasyprint.HTML(string=html_str).write_pdf()

    disposition = "attachment" if request.GET.get("download") else "inline"
    filename    = f"Invoice_{voucher.number}.pdf"

    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return response
