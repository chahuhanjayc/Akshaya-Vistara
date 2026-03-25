"""
reports/utils.py

Pure calculation functions for all financial reports.
All functions receive a Company object and date parameters.
All return plain Python dicts/lists — no ORM objects passed to templates directly.
"""

import re
from decimal import Decimal
from datetime import date, timedelta

from django.db.models import Sum
from django.db.models.functions import TruncMonth

from ledger.models import Ledger
from vouchers.models import Voucher, VoucherItem

ZERO = Decimal("0.00")

# Keywords used to identify GST/tax ledgers by name
GST_KEYWORDS = ("CGST", "SGST", "IGST", "UTGST", "GST", "VAT", "TAX PAYABLE", "INPUT TAX")

# GSTIN regex pattern
_GSTIN_RE = re.compile(r'\b([0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b')


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _coerce(val):
    """Coerce None / float to Decimal safely."""
    if val is None:
        return ZERO
    return Decimal(str(val))


def _ledger_net(ledger, start_date=None, end_date=None):
    """Return (total_dr, total_cr) for a ledger within an optional date range."""
    qs = VoucherItem.objects.filter(ledger=ledger)
    if start_date:
        qs = qs.filter(voucher__date__gte=start_date)
    if end_date:
        qs = qs.filter(voucher__date__lte=end_date)
    agg = qs.aggregate(total_dr=Sum("debit"), total_cr=Sum("credit"))
    return _coerce(agg["total_dr"]), _coerce(agg["total_cr"])


def _is_gst_ledger(ledger):
    """
    Return True if the ledger represents a GST/tax component.
    Matches by:
      1. group == "Tax"  (if user creates ledger with Tax group)
      2. Name contains CGST / SGST / IGST / UTGST / GST / etc.
    """
    name_upper = ledger.name.upper()
    return ledger.group == "Tax" or any(kw in name_upper for kw in GST_KEYWORDS)


# ─────────────────────────────────────────────────────────────────────────────
# PROFIT & LOSS
# ─────────────────────────────────────────────────────────────────────────────

def get_profit_loss(company, start_date, end_date):
    """
    Returns:
        income_items:  [{'name', 'amount'}]
        expense_items: [{'name', 'amount'}]
        total_income, total_expense, net_profit: Decimal
    """
    base_qs = VoucherItem.objects.filter(
        ledger__company=company,
        voucher__date__gte=start_date,
        voucher__date__lte=end_date,
    )

    income_rows = (
        base_qs.filter(ledger__group="Income")
        .values("ledger__pk", "ledger__name")
        .annotate(total_dr=Sum("debit"), total_cr=Sum("credit"))
        .order_by("ledger__name")
    )
    income_items, total_income = [], ZERO
    for row in income_rows:
        net = _coerce(row["total_cr"]) - _coerce(row["total_dr"])
        income_items.append({"name": row["ledger__name"], "amount": net})
        total_income += net

    expense_rows = (
        base_qs.filter(ledger__group="Expense")
        .values("ledger__pk", "ledger__name")
        .annotate(total_dr=Sum("debit"), total_cr=Sum("credit"))
        .order_by("ledger__name")
    )
    expense_items, total_expense = [], ZERO
    for row in expense_rows:
        net = _coerce(row["total_dr"]) - _coerce(row["total_cr"])
        expense_items.append({"name": row["ledger__name"], "amount": net})
        total_expense += net

    return {
        "income_items":  income_items,
        "expense_items": expense_items,
        "total_income":  total_income,
        "total_expense": total_expense,
        "net_profit":    total_income - total_expense,
    }


# ─────────────────────────────────────────────────────────────────────────────
# BALANCE SHEET
# ─────────────────────────────────────────────────────────────────────────────

def get_balance_sheet(company, as_of_date):
    """
    Returns:
        asset_items, liability_items: [{'name', 'balance'}]
        total_assets, total_liabilities, difference: Decimal
    """
    asset_items, total_assets = [], ZERO
    for ledger in Ledger.objects.filter(company=company, group="Asset").order_by("name"):
        dr, cr = _ledger_net(ledger, end_date=as_of_date)
        balance = ledger.opening_balance + dr - cr
        asset_items.append({"name": ledger.name, "balance": balance})
        total_assets += balance

    liability_items, total_liabilities = [], ZERO
    for ledger in Ledger.objects.filter(company=company, group="Liability").order_by("name"):
        dr, cr = _ledger_net(ledger, end_date=as_of_date)
        balance = ledger.opening_balance + cr - dr
        liability_items.append({"name": ledger.name, "balance": balance})
        total_liabilities += balance

    # Retained Earnings (Net Profit) goes on Liabilities + Equity side
    inc_agg = VoucherItem.objects.filter(
        ledger__company=company, ledger__group="Income",
        voucher__date__lte=as_of_date,
    ).aggregate(cr=Sum("credit"), dr=Sum("debit"))
    exp_agg = VoucherItem.objects.filter(
        ledger__company=company, ledger__group="Expense",
        voucher__date__lte=as_of_date,
    ).aggregate(cr=Sum("credit"), dr=Sum("debit"))

    retained = (
        (_coerce(inc_agg["cr"]) - _coerce(inc_agg["dr"]))
        - (_coerce(exp_agg["dr"]) - _coerce(exp_agg["cr"]))
    )
    if retained != ZERO:
        liability_items.append({
            "name": "Retained Earnings (Net Profit)",
            "balance": retained,
            "is_retained": True,
        })
        total_liabilities += retained

    return {
        "asset_items":       asset_items,
        "liability_items":   liability_items,
        "total_assets":      total_assets,
        "total_liabilities": total_liabilities,
        "difference":        total_assets - total_liabilities,
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRIAL BALANCE
# ─────────────────────────────────────────────────────────────────────────────

def get_trial_balance(company, start_date, end_date):
    """
    For every active ledger returns:
      opening_dr / opening_cr  — balance as of day before start_date
      period_dr  / period_cr   — movements in [start_date, end_date]
      closing_dr / closing_cr  — balance as of end_date

    The sum of period_dr must equal period_cr (double-entry integrity).
    The sum of closing_dr must equal closing_cr (books in balance).
    """
    rows = []
    tot_open_dr = tot_open_cr = ZERO
    tot_per_dr  = tot_per_cr  = ZERO
    tot_clos_dr = tot_clos_cr = ZERO

    for ledger in Ledger.objects.filter(company=company, is_active=True).order_by("group", "name"):
        is_dr_nature = ledger.group in ("Asset", "Expense")

        # Pre-period movements (all transactions before start_date)
        pre = VoucherItem.objects.filter(
            ledger=ledger, voucher__date__lt=start_date
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        pre_dr = _coerce(pre["dr"])
        pre_cr = _coerce(pre["cr"])

        # Opening balance in natural sign (positive = natural side)
        if is_dr_nature:
            opening = ledger.opening_balance + pre_dr - pre_cr
            opening_dr = max(opening, ZERO)
            opening_cr = max(-opening, ZERO)
        else:
            opening = ledger.opening_balance + pre_cr - pre_dr
            opening_cr = max(opening, ZERO)
            opening_dr = max(-opening, ZERO)

        # Period movements
        period = VoucherItem.objects.filter(
            ledger=ledger,
            voucher__date__gte=start_date,
            voucher__date__lte=end_date,
        ).aggregate(dr=Sum("debit"), cr=Sum("credit"))
        period_dr = _coerce(period["dr"])
        period_cr = _coerce(period["cr"])

        # Closing balance
        if is_dr_nature:
            closing = opening + period_dr - period_cr
            closing_dr = max(closing, ZERO)
            closing_cr = max(-closing, ZERO)
        else:
            closing = opening + period_cr - period_dr
            closing_cr = max(closing, ZERO)
            closing_dr = max(-closing, ZERO)

        tot_open_dr += opening_dr
        tot_open_cr += opening_cr
        tot_per_dr  += period_dr
        tot_per_cr  += period_cr
        tot_clos_dr += closing_dr
        tot_clos_cr += closing_cr

        rows.append({
            "name":       ledger.name,
            "group":      ledger.get_group_display(),
            "opening_dr": opening_dr,
            "opening_cr": opening_cr,
            "period_dr":  period_dr,
            "period_cr":  period_cr,
            "closing_dr": closing_dr,
            "closing_cr": closing_cr,
        })

    is_balanced = abs(tot_per_dr - tot_per_cr) < Decimal("0.01")

    return {
        "rows":           rows,
        "tot_open_dr":    tot_open_dr,
        "tot_open_cr":    tot_open_cr,
        "tot_per_dr":     tot_per_dr,
        "tot_per_cr":     tot_per_cr,
        "tot_clos_dr":    tot_clos_dr,
        "tot_clos_cr":    tot_clos_cr,
        "is_balanced":    is_balanced,
        "difference":     abs(tot_per_dr - tot_per_cr),
    }


# ─────────────────────────────────────────────────────────────────────────────
# GST REPORT  (GSTR-1 + GSTR-3B summary)
# ─────────────────────────────────────────────────────────────────────────────

def get_gst_report(company, start_date, end_date):
    """
    GSTR-1:  Outward supplies (Sales vouchers) — taxable value + output tax,
             split B2B (buyer GSTIN found in narration) vs B2C.
    GSTR-3B: Input Tax Credit from Purchases vs Output Tax Payable → Net liability.

    GST ledger detection (in order):
      1. ledger.group == "Tax"
      2. Name contains CGST / SGST / IGST / UTGST / GST (case-insensitive)
    """
    own_gstin = (company.gstin or "").strip().upper()

    # ── GSTR-1: Sales ─────────────────────────────────────────────────────────
    sales_vouchers = (
        Voucher.objects.filter(
            company=company,
            voucher_type="Sales",
            date__gte=start_date,
            date__lte=end_date,
        )
        .prefetch_related("items__ledger")
        .order_by("date")
    )

    gstr1_rows = []
    tot_taxable_sales = tot_out_cgst = tot_out_sgst = tot_out_igst = ZERO

    for v in sales_vouchers:
        taxable_value = cgst = sgst = igst = other_gst = ZERO

        for item in v.items.all():
            if _is_gst_ledger(item.ledger):
                name_upper = item.ledger.name.upper()
                amt = item.credit - item.debit  # output tax: credit side
                if "CGST" in name_upper:
                    cgst += amt
                elif "SGST" in name_upper or "UTGST" in name_upper:
                    sgst += amt
                elif "IGST" in name_upper:
                    igst += amt
                else:
                    other_gst += amt
            elif item.ledger.group == "Income":
                taxable_value += item.credit - item.debit

        total_tax = cgst + sgst + igst + other_gst

        # Try extracting buyer GSTIN from voucher narration
        buyer_gstin = None
        if v.narration:
            m = _GSTIN_RE.search(v.narration.upper())
            if m and m.group(1) != own_gstin:
                buyer_gstin = m.group(1)

        gstr1_rows.append({
            "voucher_number": v.number,
            "date":           v.date,
            "narration":      (v.narration or "")[:60],
            "buyer_gstin":    buyer_gstin,
            "supply_type":    "B2B" if buyer_gstin else "B2C",
            "taxable_value":  taxable_value,
            "cgst":           cgst,
            "sgst":           sgst,
            "igst":           igst,
            "other_gst":      other_gst,
            "total_tax":      total_tax,
            "invoice_value":  taxable_value + total_tax,
        })

        tot_taxable_sales += taxable_value
        tot_out_cgst += cgst
        tot_out_sgst += sgst
        tot_out_igst += igst

    tot_out_tax = tot_out_cgst + tot_out_sgst + tot_out_igst

    # ── GSTR-3B: Input Tax Credit from Purchases ─────────────────────────────
    purchase_vouchers = (
        Voucher.objects.filter(
            company=company,
            voucher_type="Purchase",
            date__gte=start_date,
            date__lte=end_date,
        )
        .prefetch_related("items__ledger")
        .order_by("date")
    )

    itc_cgst = itc_sgst = itc_igst = itc_other = ZERO
    tot_taxable_purchases = ZERO

    for v in purchase_vouchers:
        for item in v.items.all():
            if _is_gst_ledger(item.ledger):
                name_upper = item.ledger.name.upper()
                amt = item.debit - item.credit  # ITC: debit side on purchases
                if amt <= ZERO:
                    continue
                if "CGST" in name_upper:
                    itc_cgst += amt
                elif "SGST" in name_upper or "UTGST" in name_upper:
                    itc_sgst += amt
                elif "IGST" in name_upper:
                    itc_igst += amt
                else:
                    itc_other += amt
            elif item.ledger.group == "Expense":
                amt = item.debit - item.credit
                if amt > ZERO:
                    tot_taxable_purchases += amt

    tot_itc = itc_cgst + itc_sgst + itc_igst + itc_other
    net_tax_payable = tot_out_tax - tot_itc

    b2b_rows = [r for r in gstr1_rows if r["supply_type"] == "B2B"]
    b2c_rows = [r for r in gstr1_rows if r["supply_type"] == "B2C"]

    return {
        # GSTR-1
        "gstr1_rows":            gstr1_rows,
        "b2b_rows":              b2b_rows,
        "b2c_rows":              b2c_rows,
        "tot_taxable_sales":     tot_taxable_sales,
        "tot_out_cgst":          tot_out_cgst,
        "tot_out_sgst":          tot_out_sgst,
        "tot_out_igst":          tot_out_igst,
        "tot_out_tax":           tot_out_tax,
        # GSTR-3B
        "itc_cgst":              itc_cgst,
        "itc_sgst":              itc_sgst,
        "itc_igst":              itc_igst,
        "itc_other":             itc_other,
        "tot_itc":               tot_itc,
        "tot_taxable_purchases": tot_taxable_purchases,
        "net_tax_payable":       net_tax_payable,
    }


# ─────────────────────────────────────────────────────────────────────────────
# RECEIVABLES AGING
# ─────────────────────────────────────────────────────────────────────────────

def get_receivables_aging(company, as_of_date):
    """
    For each Sales voucher with an Asset-ledger debit entry:
      1. Calculate original receivable
      2. Calculate how much settled via Receipt / Contra / Payment
      3. If outstanding > 0, bucket by days overdue from due_date
    """
    sales_vouchers = (
        Voucher.objects.filter(
            company=company, voucher_type="Sales", date__lte=as_of_date,
        )
        .prefetch_related("items__ledger")
        .order_by("date")
    )

    buckets = {"current": [], "thirty": [], "sixty": [], "ninety": []}
    totals  = {"current": ZERO, "thirty": ZERO, "sixty": ZERO, "ninety": ZERO}

    for voucher in sales_vouchers:
        receivable_items = [
            item for item in voucher.items.all()
            if item.ledger.group == "Asset" and item.debit > 0
        ]
        if not receivable_items:
            continue

        for item in receivable_items:
            total_invoiced = _coerce(
                VoucherItem.objects.filter(
                    ledger=item.ledger, voucher__company=company,
                    voucher__voucher_type="Sales", voucher__date__lte=as_of_date,
                ).aggregate(s=Sum("debit"))["s"]
            )
            total_settled = _coerce(
                VoucherItem.objects.filter(
                    ledger=item.ledger, voucher__company=company,
                    voucher__voucher_type__in=["Receipt", "Contra", "Payment"],
                    voucher__date__lte=as_of_date,
                ).aggregate(s=Sum("credit"))["s"]
            )

            outstanding = total_invoiced - total_settled
            if outstanding <= ZERO:
                continue

            due = voucher.due_date or (voucher.date + timedelta(days=30))
            days_overdue = max(0, (as_of_date - due).days)
            customer_name = (voucher.narration[:40] if voucher.narration else item.ledger.name)

            entry = {
                "voucher":       voucher,
                "customer_name": customer_name,
                "ledger_name":   item.ledger.name,
                "original":      total_invoiced,
                "settled":       total_settled,
                "outstanding":   outstanding,
                "due_date":      due,
                "days_overdue":  days_overdue,
            }

            if days_overdue <= 30:
                buckets["current"].append(entry); totals["current"] += outstanding
            elif days_overdue <= 60:
                buckets["thirty"].append(entry);  totals["thirty"]  += outstanding
            elif days_overdue <= 90:
                buckets["sixty"].append(entry);   totals["sixty"]   += outstanding
            else:
                buckets["ninety"].append(entry);  totals["ninety"]  += outstanding

    totals["grand"] = sum(totals[k] for k in ("current", "thirty", "sixty", "ninety"))
    return {"buckets": buckets, "totals": totals}


# ─────────────────────────────────────────────────────────────────────────────
# CASH FLOW (Dashboard chart — last N months)
# ─────────────────────────────────────────────────────────────────────────────

def get_monthly_cash_flow(company, months=12):
    """
    Returns monthly inflow/outflow for the last `months` months.
    Inflow  = credits on Receipt + Sales vouchers
    Outflow = debits  on Payment + Purchase vouchers
    """
    cutoff = date.today().replace(day=1)
    m = cutoff.month - months
    y = cutoff.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    start = date(y, m, 1)

    inflow_qs = (
        VoucherItem.objects.filter(
            ledger__company=company,
            voucher__date__gte=start,
            voucher__voucher_type__in=["Receipt", "Sales"],
        )
        .annotate(month=TruncMonth("voucher__date"))
        .values("month")
        .annotate(total=Sum("credit"))
        .order_by("month")
    )
    outflow_qs = (
        VoucherItem.objects.filter(
            ledger__company=company,
            voucher__date__gte=start,
            voucher__voucher_type__in=["Payment", "Purchase"],
        )
        .annotate(month=TruncMonth("voucher__date"))
        .values("month")
        .annotate(total=Sum("debit"))
        .order_by("month")
    )

    inflow_map  = {row["month"].strftime("%Y-%m"): float(row["total"] or 0) for row in inflow_qs}
    outflow_map = {row["month"].strftime("%Y-%m"): float(row["total"] or 0) for row in outflow_qs}

    labels, inflow_data, outflow_data = [], [], []
    cursor = start
    while cursor <= cutoff:
        key = cursor.strftime("%Y-%m")
        labels.append(cursor.strftime("%b %y"))
        inflow_data.append(round(inflow_map.get(key, 0.0), 2))
        outflow_data.append(round(outflow_map.get(key, 0.0), 2))
        cursor = cursor.replace(month=cursor.month + 1) if cursor.month < 12 \
                 else cursor.replace(year=cursor.year + 1, month=1)

    return {"labels": labels, "inflow": inflow_data, "outflow": outflow_data}
