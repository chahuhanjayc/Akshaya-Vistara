"""
inventory/forms.py

Forms for Stock Item CRUD and the inline stock-item rows on Vouchers.
"""

from django import forms
from django.forms import inlineformset_factory

from .models import StockItem, VoucherStockItem
from vouchers.models import Voucher


# ─────────────────────────────────────────────────────────────────────────────
# StockItem create / edit form
# ─────────────────────────────────────────────────────────────────────────────

class StockItemForm(forms.ModelForm):
    class Meta:
        model  = StockItem
        fields = [
            "name", "unit", "opening_quantity",
            "purchase_price", "selling_price",
            "hsn_sac", "tax_rate",
            "low_stock_threshold", "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Basmati Rice 5kg Bag",
                "autofocus": True,
            }),
            "unit": forms.Select(attrs={"class": "form-select"}),
            "opening_quantity": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.001", "min": "0",
            }),
            "purchase_price": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "Default purchase price per unit",
            }),
            "selling_price": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.01", "min": "0",
                "placeholder": "Default selling price per unit",
            }),
            "hsn_sac": forms.Select(attrs={"class": "form-select"}),
            "tax_rate": forms.Select(attrs={"class": "form-select"}),
            "low_stock_threshold": forms.NumberInput(attrs={
                "class": "form-control", "step": "0.001", "min": "0",
                "placeholder": "0 = no alert",
            }),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "low_stock_threshold": "Low Stock Alert Threshold",
        }
        help_texts = {
            "opening_quantity": "Current stock on hand when setting up this item.",
            "low_stock_threshold": "You will be alerted when closing stock falls below this quantity.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Make HSN and TaxRate optional
        self.fields["hsn_sac"].required  = False
        self.fields["tax_rate"].required = False
        self.fields["hsn_sac"].empty_label  = "— None —"
        self.fields["tax_rate"].empty_label = "— None —"


# ─────────────────────────────────────────────────────────────────────────────
# VoucherStockItem inline row (used in voucher_form.html)
# ─────────────────────────────────────────────────────────────────────────────

class VoucherStockItemForm(forms.ModelForm):
    class Meta:
        model  = VoucherStockItem
        fields = ["stock_item", "quantity", "rate"]
        widgets = {
            "stock_item": forms.Select(attrs={
                "class": "form-select stock-item-select",
            }),
            "quantity": forms.NumberInput(attrs={
                "class": "form-control stock-qty",
                "step": "0.001", "min": "0.001",
                "placeholder": "Qty",
            }),
            "rate": forms.NumberInput(attrs={
                "class": "form-control stock-rate",
                "step": "0.01", "min": "0",
                "placeholder": "Rate ₹",
            }),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company:
            self.fields["stock_item"].queryset = StockItem.objects.filter(
                company=company, is_active=True
            ).order_by("name")
        else:
            self.fields["stock_item"].queryset = StockItem.objects.none()
        self.fields["stock_item"].empty_label = "— Select Item —"
        self.fields["stock_item"].required    = False  # Allow empty rows


# Formset factory — attached to a Voucher
VoucherStockItemFormSet = inlineformset_factory(
    Voucher,
    VoucherStockItem,
    form=VoucherStockItemForm,
    extra=3,
    min_num=0,
    validate_min=False,
    can_delete=True,
)
