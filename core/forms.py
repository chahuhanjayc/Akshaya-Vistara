"""
core/forms.py
Forms for core app — company creation and company settings.
"""

from django import forms
from .models import Company


class CompanySettingsForm(forms.ModelForm):
    """
    Lets an Admin update both the company profile (name, GSTIN, address)
    and the banking / UPI payment details added in Phase 6.
    """

    class Meta:
        model = Company
        fields = [
            # ── Profile ───────────────────────────────────────────────────────
            "name",
            "short_code",
            "gstin",
            "address",
            "financial_year_start",
            # ── Banking & UPI ─────────────────────────────────────────────────
            "upi_id",
            "bank_name",
            "account_number",
            "ifsc_code",
        ]
        widgets = {
            "name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. Acme Trading Co.",
            }),
            "short_code": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "ATC (auto-generated if blank)",
                "maxlength": "6",
            }),
            "gstin": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "22AAAAA0000A1Z5",
                "maxlength": "15",
            }),
            "address": forms.Textarea(attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Registered office address (shown on invoices)",
            }),
            "financial_year_start": forms.DateInput(attrs={
                "class": "form-control",
                "type": "date",
            }),
            # Banking
            "upi_id": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. business@ybl",
            }),
            "bank_name": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. State Bank of India",
            }),
            "account_number": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. 00000123456789",
            }),
            "ifsc_code": forms.TextInput(attrs={
                "class": "form-control",
                "placeholder": "e.g. SBIN0001234",
                "maxlength": "11",
            }),
        }
        labels = {
            "short_code":          "Voucher Prefix",
            "financial_year_start": "Financial Year Start",
        }
        help_texts = {
            "short_code": "Up to 6 characters used as prefix in voucher numbers (e.g. ABC → ABC2526-00001).",
            "upi_id":     "Setting this enables a Pay-Now QR code on all Sales invoices.",
            "ifsc_code":  "11-character code printed on invoices (e.g. SBIN0001234).",
        }

    def clean_gstin(self):
        gstin = self.cleaned_data.get("gstin", "").strip().upper()
        if gstin and len(gstin) != 15:
            raise forms.ValidationError("GSTIN must be exactly 15 characters.")
        return gstin or None

    def clean_ifsc_code(self):
        ifsc = self.cleaned_data.get("ifsc_code", "").strip().upper()
        if ifsc and len(ifsc) != 11:
            raise forms.ValidationError("IFSC code must be exactly 11 characters.")
        return ifsc or None

    def clean_short_code(self):
        sc = self.cleaned_data.get("short_code", "").strip().upper()
        return sc or None
