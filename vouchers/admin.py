"""
vouchers/admin.py
"""

from django.contrib import admin
from django.utils.html import format_html
from .models import Voucher, VoucherItem, VoucherSequence


class VoucherItemInline(admin.TabularInline):
    model = VoucherItem
    fk_name = "voucher"          # disambiguates from reference_voucher FK
    extra = 2
    fields = ["ledger", "debit", "credit", "narration", "reference_voucher"]
    autocomplete_fields = ["ledger"]


@admin.register(Voucher)
class VoucherAdmin(admin.ModelAdmin):
    list_display = [
        "number", "company", "voucher_type", "date",
        "balanced_status", "narration", "created_at",
    ]
    list_filter = ["voucher_type", "company", "date"]
    search_fields = ["number", "narration", "company__name"]
    readonly_fields = ["number", "created_at", "updated_at"]
    date_hierarchy = "date"
    inlines = [VoucherItemInline]
    list_select_related = ["company"]

    fieldsets = (
        (None, {"fields": ("company", "number", "voucher_type", "date", "narration")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def balanced_status(self, obj):
        if obj.is_balanced():
            return format_html('<span style="color:green;font-weight:bold;">✔ Balanced</span>')
        return format_html('<span style="color:red;font-weight:bold;">✘ Unbalanced</span>')

    balanced_status.short_description = "Status"


@admin.register(VoucherItem)
class VoucherItemAdmin(admin.ModelAdmin):
    list_display = ["voucher", "ledger", "debit", "credit"]
    search_fields = ["voucher__number", "ledger__name"]
    list_select_related = ["voucher", "ledger"]
    autocomplete_fields = ["ledger"]


@admin.register(VoucherSequence)
class VoucherSequenceAdmin(admin.ModelAdmin):
    list_display = ["company", "financial_year", "last_number"]
    list_filter = ["company", "financial_year"]
    readonly_fields = ["company", "financial_year", "last_number"]
