"""
vouchers/urls.py
"""

from django.urls import path
from . import views

app_name = "vouchers"

urlpatterns = [
    path("",                             views.voucher_list,          name="list"),
    path("create/",                      views.voucher_create,        name="create"),
    path("bulk/",                        views.bulk_action,           name="bulk_action"),
    path("outstanding/",                 views.outstanding_statement, name="outstanding"),
    path("<int:pk>/",                    views.voucher_detail,        name="detail"),
    path("<int:pk>/edit/",               views.voucher_edit,          name="edit"),
    path("<int:pk>/delete/",             views.voucher_delete,        name="delete"),
    path("<int:pk>/simulate-payment/",   views.simulate_payment,      name="simulate_payment"),
    path("<int:pk>/pdf/",                views.invoice_pdf,            name="invoice_pdf"),
]
