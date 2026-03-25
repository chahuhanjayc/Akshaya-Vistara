"""
vouchers/models.py

Voucher + VoucherItem models.

DOUBLE ENTRY RULE:
  Every voucher saved must have ΣDebit == ΣCredit.
  Enforced at model level (clean) AND in the view (atomic transaction).

VOUCHER NUMBER FORMAT:
  {ShortCode}{FY}-{SEQUENCE:05d}   e.g.  ABC2425-00001
  Generated thread-safely with select_for_update on VoucherSequence.

BILL-TO-BILL TRACKING:
  VoucherItem.reference_voucher is an optional FK back to another Voucher.
  It records "this payment/receipt line settles that specific invoice."
  Used by the Outstanding Statement report to compute unsettled balances.
"""

from decimal import Decimal
from django.db import models, transaction
from django.core.exceptions import ValidationError
from django.utils import timezone

from core.models import Company
from ledger.models import Ledger


# ---------------------------------------------------------------------------
# Sequence counter — one row per (company, financial_year)
# ---------------------------------------------------------------------------
class VoucherSequence(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE)
    financial_year = models.CharField(max_length=10, help_text="e.g. 2425")
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("company", "financial_year")

    def __str__(self):
        return f"{self.company.short_code}{self.financial_year}-{self.last_number:05d}"


# ---------------------------------------------------------------------------
# Voucher
# ---------------------------------------------------------------------------
class Voucher(models.Model):
    VOUCHER_TYPE_CHOICES = [
        ("Payment", "Payment"),
        ("Receipt", "Receipt"),
        ("Sales", "Sales"),
        ("Purchase", "Purchase"),
        ("Contra", "Contra"),
        ("Journal", "Journal"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="vouchers"
    )
    number = models.CharField(max_length=30, blank=True, editable=False)
    date = models.DateField(default=timezone.now)
    due_date = models.DateField(
        null=True, blank=True,
        help_text="Payment due date — used for Receivables Aging report.",
    )
    voucher_type = models.CharField(max_length=20, choices=VOUCHER_TYPE_CHOICES)
    narration = models.TextField(blank=True, help_text="Brief description / memo")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Voucher"
        verbose_name_plural = "Vouchers"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.number} | {self.voucher_type} | {self.date}"

    # ------------------------------------------------------------------
    # Thread-safe voucher number generation
    # ------------------------------------------------------------------
    @staticmethod
    def _financial_year_code(date):
        """Return 4-char code like '2425' for FY 2024-25."""
        year = date.year
        month = date.month
        if month >= 4:  # April onwards → new FY
            fy_start = year
            fy_end = year + 1
        else:
            fy_start = year - 1
            fy_end = year
        return f"{str(fy_start)[2:]}{str(fy_end)[2:]}"

    def generate_number(self):
        """Generate the next voucher number for this company + FY. Thread-safe."""
        fy_code = self._financial_year_code(self.date)

        with transaction.atomic():
            seq, _ = VoucherSequence.objects.select_for_update().get_or_create(
                company=self.company,
                financial_year=fy_code,
                defaults={"last_number": 0},
            )
            seq.last_number += 1
            seq.save(update_fields=["last_number"])
            short = self.company.short_code or "VCH"
            return f"{short}{fy_code}-{seq.last_number:05d}"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self.generate_number()
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Double-entry validation
    # ------------------------------------------------------------------
    def clean(self):
        """Validate that ΣDebit == ΣCredit across all items."""
        # Only validate if the voucher already has items (editing)
        if self.pk:
            items = self.items.all()
            if not items.exists():
                raise ValidationError("A voucher must have at least one line item.")
            total_dr = sum(i.debit for i in items)
            total_cr = sum(i.credit for i in items)
            if total_dr != total_cr:
                raise ValidationError(
                    f"Voucher is not balanced. "
                    f"Total Debit = {total_dr:.2f}, Total Credit = {total_cr:.2f}. "
                    f"Difference = {abs(total_dr - total_cr):.2f}"
                )

    def is_balanced(self):
        items = self.items.all()
        if not items.exists():
            return False
        return sum(i.debit for i in items) == sum(i.credit for i in items)

    def total_debit(self):
        return sum(i.debit for i in self.items.all())

    def total_credit(self):
        return sum(i.credit for i in self.items.all())

    # ------------------------------------------------------------------
    # Bill-to-Bill helpers
    # ------------------------------------------------------------------
    def amount_settled(self):
        """
        Total amount received/paid against this voucher via reference_voucher links.
        Sums all VoucherItem.credit values where reference_voucher == this voucher.
        Works for Sales invoices (credit items on the Debtor side of Receipts settle them).
        """
        from decimal import Decimal
        result = VoucherItem.objects.filter(
            reference_voucher=self
        ).aggregate(
            settled=models.Sum("credit")
        )["settled"] or Decimal("0.00")
        return result

    def outstanding_amount(self):
        """Invoice total minus what has been settled. Always >= 0."""
        invoice_total = self.total_debit()
        settled = self.amount_settled()
        outstanding = invoice_total - settled
        return max(outstanding, Decimal("0.00"))

    def is_fully_settled(self):
        return self.outstanding_amount() == Decimal("0.00")


# ---------------------------------------------------------------------------
# VoucherItem
# ---------------------------------------------------------------------------
class VoucherItem(models.Model):
    voucher = models.ForeignKey(
        Voucher, on_delete=models.CASCADE, related_name="items"
    )
    ledger = models.ForeignKey(
        Ledger, on_delete=models.PROTECT, related_name="voucher_items"
    )
    debit  = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    credit = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal("0.00"))
    narration = models.CharField(max_length=255, blank=True)

    # Bill-to-Bill: optionally link this payment/receipt line to a specific
    # original invoice (Sales or Purchase voucher) it is settling.
    reference_voucher = models.ForeignKey(
        Voucher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="settlements",
        help_text=(
            "The Sales/Purchase invoice this line is settling. "
            "Leave blank if not applicable."
        ),
    )

    class Meta:
        verbose_name = "Voucher Item"
        verbose_name_plural = "Voucher Items"

    def __str__(self):
        return (
            f"{self.ledger.name} | Dr {self.debit:.2f} | Cr {self.credit:.2f}"
        )

    def clean(self):
        """Each row must have exactly one non-zero side (or both zero is fine for blank rows)."""
        if self.debit < 0 or self.credit < 0:
            raise ValidationError("Debit and Credit amounts cannot be negative.")
        if self.debit > 0 and self.credit > 0:
            raise ValidationError(
                "A single voucher item cannot have both Debit and Credit amounts. "
                "Split them into separate lines."
            )
