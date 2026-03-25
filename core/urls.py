"""
core/urls.py
"""

from django.urls import path
from . import views

app_name = "core"

urlpatterns = [
    path("select-company/",                  views.select_company,   name="select_company"),
    path("switch-company/<int:company_id>/", views.switch_company,   name="switch_company"),
    path("dashboard/",                       views.dashboard,        name="dashboard"),
    path("settings/",                        views.company_settings, name="company_settings"),
]
