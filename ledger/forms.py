"""
ledger/forms.py
"""

from django import forms
from .models import Ledger


class LedgerForm(forms.ModelForm):
    class Meta:
        model = Ledger
        fields = ["name", "group", "opening_balance", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "group": forms.Select(attrs={"class": "form-select"}),
            "opening_balance": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
