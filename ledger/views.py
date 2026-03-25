"""
ledger/views.py

Access control:
  list        → all authenticated users with company access
  create      → Admin, Accountant
  edit        → Admin, Accountant
  delete      → Admin only
  quick_add   → Admin, Accountant (AJAX endpoint for inline modal in voucher form)
"""

import json
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from core.decorators import admin_required, write_required
from .models import Ledger
from .forms import LedgerForm


@login_required
def ledger_list(request):
    company = request.current_company
    show_inactive = request.GET.get("show_inactive") == "1"
    qs = Ledger.objects.filter(company=company)
    if not show_inactive:
        qs = qs.filter(is_active=True)
    ledgers = qs.order_by("group", "name")
    inactive_count = Ledger.objects.filter(company=company, is_active=False).count()
    return render(request, "ledger/ledger_list.html", {
        "ledgers": ledgers,
        "show_inactive": show_inactive,
        "inactive_count": inactive_count,
    })


@login_required
@write_required
def ledger_create(request):
    company = request.current_company
    form = LedgerForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        ledger = form.save(commit=False)
        ledger.company = company
        ledger.save()
        messages.success(request, f'Ledger "{ledger.name}" created successfully.')
        return redirect("ledger:list")

    return render(request, "ledger/ledger_form.html", {"form": form, "title": "Create Ledger"})


@login_required
@write_required
def ledger_edit(request, pk):
    company = request.current_company
    ledger  = get_object_or_404(Ledger, pk=pk, company=company)
    form    = LedgerForm(request.POST or None, instance=ledger)

    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f'Ledger "{ledger.name}" updated.')
        return redirect("ledger:list")

    return render(request, "ledger/ledger_form.html",
                  {"form": form, "title": "Edit Ledger", "ledger": ledger})


@login_required
@admin_required
def ledger_deactivate(request, pk):
    """Soft-delete: marks ledger as inactive instead of hard-deleting."""
    company = request.current_company
    ledger  = get_object_or_404(Ledger, pk=pk, company=company)

    if request.method == "POST":
        ledger.is_active = False
        ledger.save(update_fields=["is_active"])
        messages.warning(request, f'Ledger "{ledger.name}" has been deactivated.')
        return redirect("ledger:list")

    return render(request, "ledger/ledger_confirm_deactivate.html", {"ledger": ledger})


@login_required
@admin_required
def ledger_reactivate(request, pk):
    """Re-activates a previously deactivated ledger."""
    company = request.current_company
    ledger  = get_object_or_404(Ledger, pk=pk, company=company)

    if request.method == "POST":
        ledger.is_active = True
        ledger.save(update_fields=["is_active"])
        messages.success(request, f'Ledger "{ledger.name}" has been reactivated.')
        return redirect("ledger:list")

    return render(request, "ledger/ledger_confirm_reactivate.html", {"ledger": ledger})


@login_required
@write_required
@require_POST
def ledger_quick_add(request):
    """
    AJAX endpoint: creates a new ledger for the current company and returns JSON.

    Expected POST body (JSON or form-encoded):
        name, group, opening_balance

    Returns:
        { "success": true,  "id": <pk>, "name": "<name>", "group": "<group>" }
        { "success": false, "errors": { field: [msgs] } }
    """
    company = request.current_company

    # Accept both JSON body and form-encoded POST
    try:
        data = json.loads(request.body)
    except (ValueError, TypeError):
        data = request.POST.dict()

    form = LedgerForm(data)
    if form.is_valid():
        ledger = form.save(commit=False)
        ledger.company = company
        # Guard: don't create duplicate names
        if Ledger.objects.filter(company=company, name__iexact=ledger.name).exists():
            return JsonResponse({
                "success": False,
                "errors": {"name": [f'A ledger named "{ledger.name}" already exists.']},
            }, status=400)
        ledger.save()
        return JsonResponse({
            "success": True,
            "id":    ledger.pk,
            "name":  ledger.name,
            "group": ledger.get_group_display(),
        })

    return JsonResponse({
        "success": False,
        "errors":  {
            field: [str(e) for e in errs]
            for field, errs in form.errors.items()
        },
    }, status=400)
