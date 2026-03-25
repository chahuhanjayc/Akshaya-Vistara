"""
tally_pro/urls.py  — Root URL configuration
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView

urlpatterns = [
    # Admin
    path("admin/", admin.site.urls),

    # Root redirect → company select (middleware handles auth)
    path("", RedirectView.as_view(url="/core/select-company/", permanent=False)),

    # Accounts (login / logout / register)
    path("accounts/", include("accounts.urls")),

    # Core (company select, dashboard)
    path("core/", include("core.urls")),

    # Ledger
    path("ledger/", include("ledger.urls")),

    # Vouchers
    path("vouchers/", include("vouchers.urls")),

    # OCR / Bill Automation
    path("ocr/", include("ocr.urls")),

    # Reports
    path("reports/", include("reports.urls")),

    # Inventory (Phase 4.1)
    path("inventory/", include("inventory.urls")),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
