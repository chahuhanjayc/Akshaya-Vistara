"""
vouchers/forms.py

VoucherForm + VoucherItemFormSet + VoucherStockItemFormSet.

The view is responsible for passing company so we filter ledger choices.
VoucherItemForm includes an optional reference_voucher field for bill-to-bill tracking.
VoucherStockItemFormSet (from inventory app) is imported here for use in voucher views.
"""

from decimal import Decimal
from django import forms
from django.forms import inlineformset_factory

from .models import Voucher, VoucherItem
from ledger.models import Ledger


class VoucherForm(forms.ModelForm):
    class Meta:
        model = Voucher
        fields = ["date", "due_date", "voucher_type", "narration"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "due_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "voucher_type": forms.Select(attrs={"class": "form-select"}),
            "narration": forms.Textarea(
                attrs={"class": "form-control", "rows": 2, "placeholder": "Optional memo…"}
            ),
        }


class VoucherItemForm(forms.ModelForm):
    class Meta:
        model = VoucherItem
        fields = ["ledger", "debit", "credit", "narration", "reference_voucher"]
        widgets = {
            "ledger": forms.Select(attrs={"class": "form-select ledger-select"}),
            "debit": forms.NumberInput(
                attrs={"class": "form-control debit-input", "step": "0.01", "min": "0"}
            ),
            "credit": forms.NumberInput(
                attrs={"class": "form-control credit-input", "step": "0.01", "min": "0"}
            ),
            "narration": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Line note (optional)"}
            ),
            "reference_voucher": forms.Select(
                attrs={
                    "class": "form-select form-select-sm ref-voucher-select",
                    "title": "Bill-to-Bill: which invoice does this line settle?",
                }
            ),
        }
        labels = {
            "reference_voucher": "Against Invoice",
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company:
            self.fields["ledger"].queryset = Ledger.objects.filter(
                company=company, is_active=True
            ).order_by("group", "name")

            # Only show Sales and Purchase vouchers as reference targets
            self.fields["reference_voucher"].queryset = Voucher.objects.filter(
                company=company,
                voucher_type__in=["Sales", "Purchase"],
            ).order_by("-date")
        else:
            self.fields["reference_voucher"].queryset = Voucher.objects.none()

        # Make reference_voucher optional — empty label
        self.fields["reference_voucher"].required = False
        self.fields["reference_voucher"].empty_label = "— None (not bill-linked) —"


# ---------------------------------------------------------------------------
# Inline formset factory — used in the view
# ---------------------------------------------------------------------------
VoucherItemFormSet = inlineformset_factory(
    Voucher,
    VoucherItem,
    form=VoucherItemForm,
    fk_name="voucher",       # disambiguates from reference_voucher FK
    extra=3,
    min_num=1,
    validate_min=True,
    can_delete=True,
)
