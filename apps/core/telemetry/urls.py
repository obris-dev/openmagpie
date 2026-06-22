from common.urls import api_path

from . import views

urlpatterns = [
    api_path("", views.TelemetryView.as_view(), name="telemetry"),
]
