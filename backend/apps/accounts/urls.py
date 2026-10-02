from django.urls import path

from . import views

urlpatterns = [
    path("config", views.AuthConfigView.as_view(), name="auth-config"),
    path("me", views.MeView.as_view(), name="auth-me"),
    path("github/login", views.GitHubLoginView.as_view(), name="auth-github-login"),
    path("github/callback", views.GitHubCallbackView.as_view(), name="auth-github-callback"),
    path("github/install", views.GitHubInstallView.as_view(), name="auth-github-install"),
    path("demo", views.DemoLoginView.as_view(), name="auth-demo"),
    path("access-request", views.AccessRequestView.as_view(), name="auth-access-request"),
    path("organization", views.SwitchOrganizationView.as_view(), name="auth-organization"),
    path("logout", views.LogoutView.as_view(), name="auth-logout"),
    path("audit", views.AuditLogView.as_view(), name="auth-audit"),
]
