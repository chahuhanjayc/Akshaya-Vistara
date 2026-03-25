"""
inventory/models.py

Basic Inventory Management — Phase 4.1.

Models:
  HSN_SAC         — HSN/SAC codes for GST classification (shared lookup table)
  TaxRate         — GST tax rate presets (0%, 5%, 12%, 18%, 28%)
  StockItem       — A product/good bought and sold by the company (multi-tenant)
  StockLedger     — Every stock movement tied to a Voucher
  VoucherStockItem— Links a Voucher to stock items with qty and rate

Design rules:
  - Multi-tenant: StockItem is always scoped to a Company.
  - StockLedger quantity > 0 → Inward (Purchase); < 0 → Outward (Sales).
  - Closing stock = opening_quantity + Σ(StockLedger.quantity).
  - Valuation: Weighted Average Cost (WAC).
  - No warehouses, no batch/serial tracking (Phase 4.2).
"""

from decimal import Decimal
from django.db import models
from django.core.exceptions import ValidationError

from core.models import Company
from vouchers.models import Voucher


# ─────────────────────────────────────────────────────────────────────────────
# Lookup Tables (shared, not company-scoped — shared across all companies)
# ─────────────────────────────────────────────────────────────────────────────

class HSN_SAC(models.Model):
    """
    HSN (Harmonized System of Nomenclature) / SAC (Services Accounting Code).
    Codes used for GST classification of goods and services.
    These are standard Indian GST codes — shared across all companies.
    """
    code        = models.CharField(max_length=20, unique=True)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name        = "HSN / SAC Code"
        verbose_name_plural = "HSN / SAC Codes"
        ordering            = ["code"]

    def __str__(self):
        if self.description:
            return f"{self.code} — {self.description[:50]}"
        return self.code


class TaxRate(models.Model):
    """
    Standard GST tax rate presets.
    Common rates: 0%, 5%, 12%, 18%, 28%.
    """
    rate        = models.DecimalField(max_digits=5, decimal_places=2)
    description = models.CharField(max_length=100, blank=True,
                                   help_text="e.g. 'GST 18%' or 'Exempt'")

    class Meta:
        verbose_name        = "Tax Rate"
        verbose_name_plural = "Tax Rates"
        ordering            = ["rate"]

    def __str__(self):
        desc = f" ({self.description})" if self.description else ""
        return f"{self.rate}%{desc}"


# ─────────────────────────────────────────────────────────────────────────────
# StockItem
# ─────────────────────────────────────────────────────────────────────────────

class StockItem(models.Model):
    """
    A product/item that the company buys and sells.

    Opening quantity is set once at creation.  All subsequent movements are
    tracked via StockLedger entries.  current_stock() computes the live qty.
    """
    UNIT_CHOICES = [
        ("Nos",    "Nos"),
        ("Kgs",    "Kgs"),
        ("Boxes",  "Boxes"),
        ("Dozen",  "Dozen"),
        ("Meters", "Meters"),
        ("Pieces", "Pieces"),
    ]

    name             = models.CharField(max_length=255)
    company          = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="stock_items"
    )
    unit             = models.CharField(max_length=20, choices=UNIT_CHOICES, default="Nos")
    opening_quantity = models.DecimalField(
        max_digits=15, decimal_places=3, default=Decimal("0.000"),
        help_text="Initial stock quantity when item was set up.",
    )
    purchase_price   = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Default purchase price per unit (used as fallback for WAC).",
    )
    selling_price    = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal("0.00"),
        help_text="Default selling price per unit (auto-fills voucher lines).",
    )
    hsn_sac          = models.ForeignKey(
        HSN_SAC, null=True, blank=True, on_delete=models.SET_NULL,
        verbose_name="HSN / SAC Code",
    )
    tax_rate         = models.ForeignKey(
        TaxRate, null=True, blank=True, on_delete=models.SET_NULL,
    )
    low_stock_threshold = models.DecimalField(
        max_digits=15, decimal_places=3, default=Decimal("0.000"),
        help_text="Show alert when closing stock falls below this level. 0 = no alert.",
    )
    is_active        = models.BooleanField(default=True)
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = "Stock Item"
        verbose_name_plural = "Stock Items"
        ordering            = ["name"]
        unique_together     = ("company", "name")

    def __str__(self):
        return f"{self.name} ({self.unit})"

    # ── Stock quantity helpers ────────────────────────────────────────────────

    def total_inward(self, start_date=None, end_date=None):
        """Sum of positive (purchase) movements."""
        qs = self.ledger_entries.filter(quantity__gt=0)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        return qs.aggregate(total=models.Sum("quantity"))["total"] or Decimal("0.000")

    def total_outward(self, start_date=None, end_date=None):
        """Absolute sum of negative (sales) movements."""
        qs = self.ledger_entries.filter(quantity__lt=0)
        if start_date:
            qs = qs.filter(date__gte=start_date)
        if end_date:
            qs = qs.filter(date__lte=end_date)
        total = qs.aggregate(total=models.Sum("quantity"))["total"] or Decimal("0.000")
        return abs(total)

    def closing_quantity(self, end_date=None):
        """
        Closing stock = opening_quantity + Σ(StockLedger.quantity up to end_date).
        Negative movements (outward) naturally subtract from the total.
        """
        qs = self.ledger_entries.all()
        if end_date:
            qs = qs.filter(date__lte=end_date)
        net = qs.aggregate(total=models.Sum("quantity"))["total"] or Decimal("0.000")
        return self.opening_quantity + net

    def weighted_average_cost(self):
        """
        WAC = Σ(purchase_qty × purchase_rate) / Σ(purchase_qty).
        Falls back to purchase_price if no StockLedger purchase entries exist.
        """
        purchases   = self.ledger_entries.filter(quantity__gt=0)
        total_qty   = (
            purchases.aggregate(t=models.Sum("quantity"))["t"] or Decimal("0.000")
        )
        if total_qty > 0:
            total_value = sum(e.quantity * e.rate for e in purchases)
            return (total_value / total_qty).quantize(Decimal("0.01"))
        return self.purchase_price

    def closing_stock_value(self, end_date=None):
        """Closing qty × WAC — used for Stock Valuation report."""
        qty = self.closing_quantity(end_date=end_date)
        wac = self.weighted_average_cost()
        return (qty * wac).quantize(Decimal("0.01"))

    def is_low_stock(self, end_date=None):
        if self.low_stock_threshold <= 0:
            return False
        return self.closing_quantity(end_date=end_date) < self.low_stock_threshold


# ─────────────────────────────────────────────────────────────────────────────
# StockLedger  — immutable movement log
# ─────────────────────────────────────────────────────────────────────────────

class StockLedger(models.Model):
    """
    One row per stock movement.

    quantity > 0  → Inward  (Purchase voucher)
    quantity < 0  → Outward (Sales voucher)

    Deleted automatically (CASCADE) when the parent Voucher is deleted.
    Always created inside a transaction.atomic() block together with the
    Voucher and VoucherItems so accounting + inventory stay in sync.
    """
    stock_item = models.ForeignKey(
        StockItem, on_delete=models.CASCADE, related_name="ledger_entries"
    )
    voucher    = models.ForeignKey(
        Voucher, on_delete=models.CASCADE, related_name="stock_movements"
    )
    date       = models.DateField()
    quantity   = models.DecimalField(
        max_digits=15, decimal_places=3,
        help_text="Positive = inward (purchase), Negative = outward (sales).",
    )
    rate       = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Rate per unit at transaction time.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Stock Ledger Entry"
        verbose_name_plural = "Stock Ledger Entries"
        ordering            = ["date", "created_at"]
        indexes             = [
            models.Index(fields=["stock_item", "date"]),
            models.Index(fields=["voucher"]),
        ]

    def __str__(self):
        direction = "Inward" if self.quantity >= 0 else "Outward"
        return (
            f"{self.stock_item.name} | {direction} {abs(self.quantity)} "
            f"{self.stock_item.unit} @ ₹{self.rate} | {self.date}"
        )

    @property
    def amount(self):
        return (abs(self.quantity) * self.rate).quantize(Decimal("0.01"))


# ─────────────────────────────────────────────────────────────────────────────
# VoucherStockItem  — link between Voucher and StockItem rows
# ─────────────────────────────────────────────────────────────────────────────

class VoucherStockItem(models.Model):
    """
    Stores each stock-item line on a Sales/Purchase voucher.

    When saved by the view (inside transaction.atomic()):
      • A StockLedger row is created/deleted to track quantity movement.
      • The double-entry VoucherItems remain the user's responsibility
        (they can see the auto-computed total amount and fill accounting lines).

    Deleted automatically (CASCADE) when the parent Voucher is deleted.
    """
    voucher    = models.ForeignKey(
        Voucher, on_delete=models.CASCADE, related_name="voucher_stock_items"
    )
    stock_item = models.ForeignKey(
        StockItem, on_delete=models.PROTECT, related_name="voucher_lines"
    )
    quantity   = models.DecimalField(
        max_digits=15, decimal_places=3,
        help_text="Quantity of this stock item in the voucher.",
    )
    rate       = models.DecimalField(
        max_digits=15, decimal_places=2,
        help_text="Rate per unit at the time of transaction.",
    )

    class Meta:
        verbose_name        = "Voucher Stock Item"
        verbose_name_plural = "Voucher Stock Items"

    def __str__(self):
        return f"{self.stock_item.name} × {self.quantity} @ ₹{self.rate}"

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError("Quantity must be greater than zero.")
        if self.rate is not None and self.rate < 0:
            raise ValidationError("Rate cannot be negative.")

    @property
    def amount(self):
        return (self.quantity * self.rate).quantize(Decimal("0.01"))
