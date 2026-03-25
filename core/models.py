"""
core/models.py
Company, UserCompanyAccess, and AuditLog models.
Every business model in the system has a FK to Company for strict multi-tenancy.
"""

from django.db import models
from django.conf import settings


class Company(models.Model):
    name = models.CharField(max_length=255)
    gstin = models.CharField(
        max_length=15, blank=True, null=True,
        verbose_name="GSTIN",
        help_text="15-character GST Identification Number (optional)",
    )
    address = models.TextField(blank=True, null=True)
    short_code = models.CharField(
        max_length=6, blank=True,
        help_text="Used in voucher number prefix, e.g. ABC for ABC Corp",
    )
    financial_year_start = models.DateField(
        null=True, blank=True, help_text="e.g. 2024-04-01"
    )

    # ── Banking & UPI payment details ────────────────────────────────────────
    upi_id = models.CharField(
        max_length=50, blank=True, null=True,
        verbose_name="UPI ID",
        help_text="e.g. business@ybl — used to generate Pay-Now QR on Sales invoices",
    )
    bank_name = models.CharField(
        max_length=100, blank=True, null=True,
        verbose_name="Bank Name",
        help_text="e.g. State Bank of India",
    )
    account_number = models.CharField(
        max_length=30, blank=True, null=True,
        verbose_name="Account Number",
    )
    ifsc_code = models.CharField(
        max_length=11, blank=True, null=True,
        verbose_name="IFSC Code",
        help_text="11-character IFSC code, e.g. SBIN0001234",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Company"
        verbose_name_plural = "Companies"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.short_code:
            words = self.name.upper().split()
            self.short_code = (
                "".join(w[0] for w in words[:4])
                if len(words) >= 2
                else self.name.upper()[:4]
            )
        super().save(*args, **kwargs)


class UserCompanyAccess(models.Model):
    ROLE_CHOICES = [
        ("Admin", "Admin"),
        ("Accountant", "Accountant"),
        ("Viewer", "Viewer"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="company_access",
    )
    company = models.ForeignKey(
        Company, on_delete=models.CASCADE, related_name="user_access",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="Accountant")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Company Access"
        verbose_name_plural = "User Company Access"
        unique_together = ("user", "company")
        ordering = ["company__name"]

    def __str__(self):
        return f"{self.user.email} → {self.company.name} [{self.role}]"


class AuditLog(models.Model):
    """
    Immutable record of every create / update / delete action on business objects.
    Written by views; never modified after creation.
    """
    ACTION_CREATE = "create"
    ACTION_UPDATE = "update"
    ACTION_DELETE = "delete"
    ACTION_CHOICES = [
        (ACTION_CREATE, "Created"),
        (ACTION_UPDATE, "Updated"),
        (ACTION_DELETE, "Deleted"),
    ]

    company = models.ForeignKey(
        Company, on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    model_name = models.CharField(max_length=50)          # e.g. "Voucher"
    object_id = models.PositiveIntegerField()              # PK of the affected object
    object_repr = models.CharField(max_length=200)         # e.g. "AAC2526-00001"
    extra = models.JSONField(default=dict, blank=True)     # optional extra context
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Audit Log"
        verbose_name_plural = "Audit Logs"
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["company", "-timestamp"]),
            models.Index(fields=["model_name", "object_id"]),
        ]

    def __str__(self):
        return (
            f"{self.get_action_display()} {self.model_name} "
            f"#{self.object_repr} by {self.user} @ {self.timestamp:%Y-%m-%d %H:%M}"
        )

    @classmethod
    def log(cls, request, action, instance, extra=None):
        """
        Convenience class method called from views:
            AuditLog.log(request, AuditLog.ACTION_CREATE, voucher)
        """
        company = getattr(request, "current_company", None)
        if company is None:
            return  # safety — no-op outside company context
        cls.objects.create(
            company=company,
            user=request.user if request.user.is_authenticated else None,
            action=action,
            model_name=type(instance).__name__,
            object_id=instance.pk,
            object_repr=str(instance),
            extra=extra or {},
        )
