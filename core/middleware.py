"""
core/middleware.py

CurrentCompanyMiddleware:
- Reads 'current_company_id' from the session.
- Attaches the Company object to request.current_company.
- If no company is selected, redirects unauthenticated pages gracefully.
- Protected views (non-exempt) redirect to /core/select-company/ if needed.
"""

from django.shortcuts import redirect
from django.urls import reverse

from .models import Company

# URL paths that are always accessible without a selected company
EXEMPT_PATHS = [
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/register/",
    "/core/select-company/",
    "/admin/",
    "/media/",   # allow media file serving without company context
]


class CurrentCompanyMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Attach current_company to the request object
        company_id = request.session.get("current_company_id")
        request.current_company = None

        if company_id and request.user.is_authenticated:
            try:
                # Only allow companies the user actually has access to
                from .models import UserCompanyAccess  # avoid circular at module level

                access = UserCompanyAccess.objects.select_related("company").get(
                    user=request.user, company_id=company_id
                )
                request.current_company = access.company
                request.current_company_role = access.role
            except UserCompanyAccess.DoesNotExist:
                # Session has a stale company id — clear it
                request.session.pop("current_company_id", None)

        # Gate: authenticated users without a selected company → redirect
        if (
            request.user.is_authenticated
            and request.current_company is None
            and not self._is_exempt(request.path)
        ):
            return redirect(reverse("core:select_company"))

        response = self.get_response(request)
        return response

    def _is_exempt(self, path):
        for exempt in EXEMPT_PATHS:
            if path.startswith(exempt):
                return True
        return False
