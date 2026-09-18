from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from projects.views import onboard_project, analyze_spec, generate_mcp_server
from tools.views import project_tools


def health(_request):
    return JsonResponse({"status": "ok", "service": "mcp-nexus-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/projects/onboard", onboard_project, name="onboard_project"),
    path("api/projects/<int:project_id>/tools", project_tools, name="project_tools"),
    path("api/app-registration/analyze-spec", analyze_spec, name="analyze_spec"),
    path("api/app-registration/generate", generate_mcp_server, name="generate_mcp_server"),
    # Swagger / OpenAPI docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
