"""
core/views.py
Company selection, dashboard, company switch, and company settings.
"""

import json
from datetime import date as _date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages

from .decorators import admin_required
from .forms import CompanySettingsForm
from .models import Company, UserCompanyAccess
from vouchers.models import Voucher
from ledger.models import Ledger
from reports.utils import get_monthly_cash_flow


@login_required
def select_company(request):
    """Show list of companies the user can access and let them choose one."""
    access_list = (
        UserCompanyAccess.objects.filter(user=request.user)
        .select_related("company")
        .order_by("company__name")
    )

    if request.method == "POST":
        company_id = request.POST.get("company_id")
        if access_list.filter(company_id=company_id).exists():
            request.session["current_company_id"] = int(company_id)
            messages.success(request, "Company selected successfully.")
            return redirect("core:dashboard")
        else:
            messages.error(request, "You do not have access to that company.")

    return render(request, "core/select_company.html", {"access_list": access_list})


@login_required
def switch_company(request, company_id):
    """Switch active company via GET (from navbar dropdown)."""
    access = UserCompanyAccess.objects.filter(
        user=request.user, company_id=company_id
    ).first()
    if access:
        request.session["current_company_id"] = company_id
        messages.success(request, f"Switched to {access.company.name}.")
    else:
        messages.error(request, "Access denied.")
    return redirect("core:dashboard")


@login_required
def dashboard(request):
    """Main dashboard with key metrics and cash flow chart for the current company."""
    company = request.current_company

    # ── Summary cards ─────────────────────────────────────────────────────
    total_vouchers = Voucher.objects.filter(company=company).count()
    total_ledgers  = Ledger.objects.filter(company=company).count()

    # OCR pending count
    from ocr.models import OCRSubmission
    ocr_pending = OCRSubmission.objects.filter(
        company=company, status=OCRSubmission.STATUS_PENDING
    ).count()

    # Recent vouchers
    recent_vouchers = (
        Voucher.objects.filter(company=company)
        .order_by("-date", "-created_at")[:10]
    )

    # Ledger balance summary by group
    ledger_summary = {}
    for grp_code, grp_label in Ledger.GROUP_CHOICES:
        ledgers = Ledger.objects.filter(company=company, group=grp_code)
        total = sum(l.current_balance() for l in ledgers)
        ledger_summary[grp_label] = float(total)

    # ── Cash flow chart (last 12 months) ──────────────────────────────────
    cf = get_monthly_cash_flow(company, months=12)

    context = {
        "total_vouchers":  total_vouchers,
        "total_ledgers":   total_ledgers,
        "ocr_pending":     ocr_pending,
        "recent_vouchers": recent_vouchers,
        "ledger_summary":  ledger_summary,
        "today":           _date.today(),
        # JSON-safe for Chart.js
        "cf_labels":  json.dumps(cf["labels"]),
        "cf_inflow":  json.dumps(cf["inflow"]),
        "cf_outflow": json.dumps(cf["outflow"]),
    }
    return render(request, "dashboard.html", context)


@login_required
@admin_required
def company_settings(request):
    """
    Admin-only view: edit the current company's profile and banking / UPI details.
    """
    company = request.current_company

    if request.method == "POST":
        form = CompanySettingsForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(
                request,
                "Company settings saved successfully."
            )
            return redirect("core:company_settings")
    else:
        form = CompanySettingsForm(instance=company)

    return render(request, "core/company_settings.html", {
        "form":    form,
        "company": company,
    })
