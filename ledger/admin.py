"""
ledger/admin.py
"""

from django.contrib import admin
from .models import Ledger


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "group", "opening_balance", "is_active", "created_at"]
    list_filter = ["group", "company", "is_active"]
    search_fields = ["name", "company__name"]
    list_select_related = ["company"]
    readonly_fields = ["created_at"]
    fieldsets = (
        (None, {"fields": ("company", "name", "group", "opening_balance", "is_active")}),
        ("Meta", {"fields": ("created_at",)}),
    )
