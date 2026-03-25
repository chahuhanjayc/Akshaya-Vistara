"""
ledger/models.py

Ledger: a named account belonging to a Company, categorised by group.
current_balance() computes the running balance from voucher items.
"""

from decimal import Decimal
from django.db import models
from core.models import Company


class Ledger(models.Model):
    GROUP_CHOICES = [
        ("Asset", "Asset"),
        ("Liability", "Liability"),
        ("Income", "Income"),
        ("Expense", "Expense"),
        ("Tax", "Tax"),          # GST / VAT / input-output tax accounts
    ]

    company = models.ForeignKey(
        Company,
        on_delete=models.CASCADE,
        related_name="ledgers",
    )
    name = models.CharField(max_length=255)
    group = models.CharField(max_length=20, choices=GROUP_CHOICES)
    opening_balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00")
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ledger"
        verbose_name_plural = "Ledgers"
        unique_together = ("company", "name")
        ordering = ["group", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_group_display()})"

    def current_balance(self):
        """
        Balance = opening_balance + sum(credits) - sum(debits) from voucher items.
        For Asset/Expense: debit-nature accounts → positive means Dr balance.
        For Liability/Income: credit-nature accounts → positive means Cr balance.
        This returns a simple net figure (Cr - Dr + opening).
        """
        from vouchers.models import VoucherItem

        items = VoucherItem.objects.filter(
            ledger=self, voucher__company=self.company
        )
        total_dr = sum(i.debit for i in items)
        total_cr = sum(i.credit for i in items)
        return self.opening_balance + total_cr - total_dr
