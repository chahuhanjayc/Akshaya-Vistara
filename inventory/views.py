"""
inventory/views.py

Views:
  stock_item_list      — Paginated list of stock items for the company
  stock_item_create    — Create a new stock item
  stock_item_edit      — Edit an existing stock item
  stock_item_deactivate— Soft-delete (Admin only)
  stock_summary        — Stock Summary report (opening, inward, outward, closing)
  stock_valuation      — Closing stock value at WAC per item
  low_stock_alert      — Items below their low_stock_threshold
  item_autocomplete    — AJAX: returns JSON list of stock items (for search)
  item_price_lookup    — AJAX: returns purchase/selling price for an item
"""

import json
from datetime import date as _date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404

from core.decorators import admin_required, write_required
from core.models import AuditLog
from .models import StockItem, StockLedger
from .forms import StockItemForm

PAGE_SIZE = 30


# ─────────────────────────────────────────────────────────────────────────────
# LIST
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def stock_item_list(request):
    company = request.current_company
    q       = request.GET.get("q", "").strip()
    show    = request.GET.get("show", "active")  # active | inactive | all

    qs = StockItem.objects.filter(company=company).select_related("hsn_sac", "tax_rate")

    if show == "inactive":
        qs = qs.filter(is_active=False)
    elif show == "all":
        pass
    else:
        qs = qs.filter(is_active=True)

    if q:
        qs = qs.filter(name__icontains=q)

    paginator = Paginator(qs, PAGE_SIZE)
    page_obj  = paginator.get_page(request.GET.get("page"))

    # Annotate each item with live closing qty (today)
    today = _date.today()
    items_with_qty = []
    for item in page_obj:
        closing = item.closing_quantity(end_date=today)
        items_with_qty.append({
            "item":    item,
            "closing": closing,
            "low":     item.is_low_stock(end_date=today),
        })

    return render(request, "inventory/stock_item_list.html", {
        "page_obj":       page_obj,
        "items_with_qty": items_with_qty,
        "q":              q,
        "show":           show,
        "total_count":    qs.count(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# CREATE
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def stock_item_create(request):
    company = request.current_company

    if request.method == "POST":
        form = StockItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.company = company
            item.save()
            AuditLog.log(request, AuditLog.ACTION_CREATE, item)
            messages.success(request, f"Stock item '{item.name}' created successfully.")
            return redirect("inventory:list")
    else:
        form = StockItemForm()

    return render(request, "inventory/stock_item_form.html", {
        "form":  form,
        "title": "Add Stock Item",
    })


# ─────────────────────────────────────────────────────────────────────────────
# EDIT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@write_required
def stock_item_edit(request, pk):
    company = request.current_company
    item    = get_object_or_404(StockItem, pk=pk, company=company)

    if request.method == "POST":
        form = StockItemForm(request.POST, instance=item)
        if form.is_valid():
            form.save()
            AuditLog.log(request, AuditLog.ACTION_UPDATE, item)
            messages.success(request, f"Stock item '{item.name}' updated.")
            return redirect("inventory:list")
    else:
        form = StockItemForm(instance=item)

    return render(request, "inventory/stock_item_form.html", {
        "form":  form,
        "item":  item,
        "title": f"Edit — {item.name}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# DEACTIVATE (soft-delete)
# ─────────────────────────────────────────────────────────────────────────────

@login_required
@admin_required
def stock_item_deactivate(request, pk):
    company = request.current_company
    item    = get_object_or_404(StockItem, pk=pk, company=company)

    if request.method == "POST":
        item.is_active = False
        item.save(update_fields=["is_active", "updated_at"])
        AuditLog.log(request, AuditLog.ACTION_UPDATE, item, extra={"action": "deactivated"})
        messages.warning(request, f"'{item.name}' deactivated.")
        return redirect("inventory:list")

    return render(request, "inventory/stock_item_confirm_deactivate.html", {"item": item})


# ─────────────────────────────────────────────────────────────────────────────
# STOCK SUMMARY REPORT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def stock_summary(request):
    """
    Table: Item | Unit | Opening Qty | Purchases (Inward) | Sales (Outward) | Closing Qty
    Filterable by date range.
    """
    company    = request.current_company
    start_date = request.GET.get("start_date", "").strip()
    end_date   = request.GET.get("end_date", "").strip()

    # Parse dates
    from datetime import datetime
    parsed_start = None
    parsed_end   = None
    try:
        if start_date:
            parsed_start = datetime.strptime(start_date, "%Y-%m-%d").date()
        if end_date:
            parsed_end   = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        parsed_start = None
        parsed_end   = None

    items = StockItem.objects.filter(
        company=company, is_active=True
    ).select_related("hsn_sac", "tax_rate").order_by("name")

    rows = []
    total_closing = Decimal("0.000")

    for item in items:
        inward  = item.total_inward(start_date=parsed_start, end_date=parsed_end)
        outward = item.total_outward(start_date=parsed_start, end_date=parsed_end)
        closing = item.closing_quantity(end_date=parsed_end)
        rows.append({
            "item":    item,
            "inward":  inward,
            "outward": outward,
            "closing": closing,
            "low":     item.is_low_stock(end_date=parsed_end),
        })
        total_closing += closing

    return render(request, "inventory/stock_summary.html", {
        "rows":          rows,
        "start_date":    start_date,
        "end_date":      end_date,
        "total_closing": total_closing,
    })


# ─────────────────────────────────────────────────────────────────────────────
# STOCK VALUATION REPORT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def stock_valuation(request):
    """
    Closing stock value at Weighted Average Cost per item.
    Shows: Item | Unit | Closing Qty | WAC Rate | Stock Value
    """
    company  = request.current_company
    as_of    = request.GET.get("as_of", "").strip()

    from datetime import datetime
    parsed_as_of = None
    try:
        if as_of:
            parsed_as_of = datetime.strptime(as_of, "%Y-%m-%d").date()
    except ValueError:
        parsed_as_of = None

    if not parsed_as_of:
        parsed_as_of = _date.today()
        as_of        = parsed_as_of.strftime("%Y-%m-%d")

    items = StockItem.objects.filter(
        company=company, is_active=True
    ).select_related("hsn_sac", "tax_rate").order_by("name")

    rows              = []
    total_stock_value = Decimal("0.00")

    for item in items:
        closing = item.closing_quantity(end_date=parsed_as_of)
        wac     = item.weighted_average_cost()
        value   = (closing * wac).quantize(Decimal("0.01"))
        rows.append({
            "item":    item,
            "closing": closing,
            "wac":     wac,
            "value":   value,
        })
        total_stock_value += value

    return render(request, "inventory/stock_valuation.html", {
        "rows":              rows,
        "as_of":             as_of,
        "total_stock_value": total_stock_value,
    })


# ─────────────────────────────────────────────────────────────────────────────
# LOW STOCK ALERT
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def low_stock_alert(request):
    """
    Items with closing qty < low_stock_threshold (and threshold > 0).
    """
    company = request.current_company
    today   = _date.today()

    # Only items where a threshold is configured
    items = StockItem.objects.filter(
        company=company, is_active=True, low_stock_threshold__gt=0
    ).select_related("hsn_sac", "tax_rate").order_by("name")

    low_items = []
    for item in items:
        closing = item.closing_quantity(end_date=today)
        if closing < item.low_stock_threshold:
            shortfall = item.low_stock_threshold - closing
            low_items.append({
                "item":      item,
                "closing":   closing,
                "threshold": item.low_stock_threshold,
                "shortfall": shortfall,
            })

    return render(request, "inventory/low_stock_alert.html", {
        "low_items": low_items,
        "today":     today,
    })


# ─────────────────────────────────────────────────────────────────────────────
# AJAX HELPERS
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def item_price_lookup(request, pk):
    """
    AJAX endpoint: given a StockItem pk, return its default purchase/selling
    price so the voucher form can auto-fill the rate field.
    """
    company = request.current_company
    try:
        item = StockItem.objects.get(pk=pk, company=company, is_active=True)
    except StockItem.DoesNotExist:
        return JsonResponse({"error": "Item not found"}, status=404)

    return JsonResponse({
        "purchase_price": str(item.purchase_price),
        "selling_price":  str(item.selling_price),
        "unit":           item.unit,
        "name":           item.name,
    })
