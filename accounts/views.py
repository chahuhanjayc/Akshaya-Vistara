"""
accounts/views.py
"""

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages

from .forms import LoginForm, RegisterForm


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:select_company")

    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"]
        password = form.cleaned_data["password"]
        user = authenticate(request, username=email, password=password)
        if user:
            login(request, user)
            return redirect("core:select_company")
        else:
            messages.error(request, "Invalid email or password.")

    return render(request, "registration/login.html", {"form": form})


def logout_view(request):
    logout(request)
    return redirect("accounts:login")


def register_view(request):
    if request.user.is_authenticated:
        return redirect("core:select_company")

    form = RegisterForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, "Account created! Please select or create a company.")
        return redirect("core:select_company")

    return render(request, "registration/register.html", {"form": form})
