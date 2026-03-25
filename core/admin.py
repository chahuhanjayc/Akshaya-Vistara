"""
core/admin.py
"""

from django.contrib import admin
from .models import Company, UserCompanyAccess, AuditLog


class UserCompanyAccessInline(admin.TabularInline):
    model = UserCompanyAccess
    extra = 1
    autocomplete_fields = ["user"]


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ["name", "short_code", "gstin", "created_at"]
    search_fields = ["name", "gstin", "short_code"]
    list_filter = ["created_at"]
    inlines = [UserCompanyAccessInline]
    readonly_fields = ["created_at"]
    fieldsets = (
        (None, {"fields": ("name", "short_code", "gstin")}),
        ("Details", {"fields": ("address", "financial_year_start")}),
        ("Banking & UPI", {"fields": ("upi_id", "bank_name", "account_number", "ifsc_code")}),
        ("Meta", {"fields": ("created_at",)}),
    )


@admin.register(UserCompanyAccess)
class UserCompanyAccessAdmin(admin.ModelAdmin):
    list_display = ["user", "company", "role", "created_at"]
    search_fields = ["user__email", "company__name"]
    list_filter = ["role", "company"]
    autocomplete_fields = ["user", "company"]


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display  = ["timestamp", "user", "action", "model_name", "object_repr", "company"]
    list_filter   = ["action", "model_name", "company"]
    search_fields = ["object_repr", "user__email", "model_name"]
    readonly_fields = [
        "company", "user", "action", "model_name",
        "object_id", "object_repr", "extra", "timestamp",
    ]
    list_select_related = ["user", "company"]
    ordering = ["-timestamp"]

    # Audit logs must never be modified or deleted via admin
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
