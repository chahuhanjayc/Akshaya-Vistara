"""
core/context_processors.py

Injects:
  - current_company  → the active Company object (or None)
  - user_companies   → all companies the logged-in user can access
"""

from .models import UserCompanyAccess


def current_company(request):
    context = {
        "current_company": getattr(request, "current_company", None),
        "current_company_role": getattr(request, "current_company_role", None),
        "user_companies": [],
    }

    if request.user.is_authenticated:
        context["user_companies"] = (
            UserCompanyAccess.objects.filter(user=request.user)
            .select_related("company")
            .order_by("company__name")
        )

    return context
