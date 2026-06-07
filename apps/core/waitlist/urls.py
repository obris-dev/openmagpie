from common.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.WaitlistSignupView.as_view(), name="waitlist_signup"),
]
