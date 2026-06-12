from common.urls import api_path

from . import device_sessions, views

urlpatterns = [
    # Browser session lifecycle.
    api_path("signup", views.SignupView.as_view(), name="auth_signup"),
    api_path("login", views.LoginView.as_view(), name="auth_login"),
    api_path("logout", views.LogoutView.as_view(), name="auth_logout"),
    api_path("me", views.MeView.as_view(), name="auth_me"),
    # CLI bearer-token lifecycle.
    api_path(
        "tokens/refresh",
        views.TokensRefreshView.as_view(),
        name="auth_tokens_refresh",
    ),
    api_path(
        "tokens/revoke",
        views.TokensRevokeView.as_view(),
        name="auth_tokens_revoke",
    ),
    # Personal access tokens (long-lived CLI credentials).
    api_path(
        "cli-tokens",
        views.CliTokensView.as_view(),
        name="auth_cli_tokens",
    ),
    api_path(
        "cli-tokens/<str:token_id>",
        views.CliTokenDetailView.as_view(),
        name="auth_cli_token_detail",
    ),
    # CLI ↔ browser handshake.
    api_path(
        "device-sessions",
        device_sessions.DeviceSessionsCreateView.as_view(),
        name="auth_device_sessions_create",
    ),
    api_path(
        "device-sessions/<str:session_id>",
        device_sessions.DeviceSessionPollView.as_view(),
        name="auth_device_session_poll",
    ),
    api_path(
        "device-sessions/<str:session_id>/info",
        device_sessions.DeviceSessionInfoView.as_view(),
        name="auth_device_session_info",
    ),
    api_path(
        "device-sessions/<str:session_id>/complete",
        device_sessions.DeviceSessionCompleteView.as_view(),
        name="auth_device_session_complete",
    ),
    api_path(
        "device-sessions/<str:session_id>/deny",
        device_sessions.DeviceSessionDenyView.as_view(),
        name="auth_device_session_deny",
    ),
    # Diagnostics.
    api_path("whoami", views.WhoamiView.as_view(), name="auth_whoami"),
]
