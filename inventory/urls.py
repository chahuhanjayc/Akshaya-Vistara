"""
inventory/urls.py
"""

from django.urls import path
from . import views

app_name = "inventory"

urlpatterns = [
    # ── Stock Items CRUD ──────────────────────────────────────────────────────
    path("",                         views.stock_item_list,       name="list"),
    path("create/",                  views.stock_item_create,     name="create"),
    path("<int:pk>/edit/",           views.stock_item_edit,       name="edit"),
    path("<int:pk>/deactivate/",     views.stock_item_deactivate, name="deactivate"),

    # ── Reports ───────────────────────────────────────────────────────────────
    path("summary/",                 views.stock_summary,         name="summary"),
    path("valuation/",               views.stock_valuation,       name="valuation"),
    path("low-stock/",               views.low_stock_alert,       name="low_stock"),

    # ── AJAX ─────────────────────────────────────────────────────────────────
    path("api/item/<int:pk>/price/", views.item_price_lookup,     name="item_price"),
]
