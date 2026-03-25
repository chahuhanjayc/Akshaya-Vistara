"""
ocr/urls.py
"""

from django.urls import path
from . import views

app_name = "ocr"

urlpatterns = [
    path("",                              views.ocr_list,               name="list"),
    path("upload/",                       views.ocr_upload,             name="upload"),
    path("<int:pk>/file/",                 views.ocr_file,               name="file"),
    path("<int:pk>/status/",              views.ocr_status,             name="status"),
    path("<int:pk>/verify/",              views.ocr_verify,             name="verify"),
    path("<int:pk>/confirm/",             views.ocr_confirm,            name="confirm"),
    path("<int:pk>/reject/",              views.ocr_reject,             name="reject"),
    # AJAX: quick-create stock item from line items table
    path("stock-item/quick-create/",      views.stock_item_quick_create, name="stock_item_quick_create"),
]
