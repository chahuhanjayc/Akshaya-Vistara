"""
reports/urls.py
"""

from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    path("",                          views.reports_home,        name="home"),
    path("profit-loss/",              views.profit_loss,         name="profit_loss"),
    path("profit-loss/export/",       views.export_pl_excel,     name="export_pl_excel"),
    path("profit-loss/pdf/",          views.profit_loss_pdf,     name="profit_loss_pdf"),
    path("balance-sheet/",            views.balance_sheet,       name="balance_sheet"),
    path("balance-sheet/export/",     views.export_bs_excel,     name="export_bs_excel"),
    path("balance-sheet/pdf/",        views.balance_sheet_pdf,   name="balance_sheet_pdf"),
    path("trial-balance/",            views.trial_balance,       name="trial_balance"),
    path("trial-balance/export/",     views.export_tb_excel,     name="export_tb_excel"),
    path("trial-balance/pdf/",        views.trial_balance_pdf,   name="trial_balance_pdf"),
    path("receivables-aging/",        views.receivables_aging,   name="receivables_aging"),
    path("gst/",                      views.gst_report,          name="gst_report"),
]
