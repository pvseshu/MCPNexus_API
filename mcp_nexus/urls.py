from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from projects.views import onboard_project, analyze_spec, generate_mcp_server, navigation_counts, dashboard_summary, list_mcp_servers, mcp_server_detail, set_catalog_visibility
from tools.views import project_tools, list_mcp_tools, mcp_tool_detail, execute_mcp_tool


def health(_request):
    return JsonResponse({"status": "ok", "service": "mcp-nexus-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/projects/onboard", onboard_project, name="onboard_project"),
    path("api/projects/<int:project_id>/tools", project_tools, name="project_tools"),
    path("api/app-registration/analyze-spec", analyze_spec, name="analyze_spec"),
    path("api/app-registration/generate", generate_mcp_server, name="generate_mcp_server"),
    path("api/mcp-servers", list_mcp_servers, name="list_mcp_servers"),
    path("api/mcp-servers/<str:server_id>", mcp_server_detail, name="mcp_server_detail"),
    path("api/mcp-servers/<str:server_id>/catalog-visibility", set_catalog_visibility, name="set_catalog_visibility"),
    path("api/mcp-tools", list_mcp_tools, name="list_mcp_tools"),
    path("api/mcp-tools/<str:tool_id>", mcp_tool_detail, name="mcp_tool_detail"),
    path("api/mcp-tools/<str:tool_id>/execute", execute_mcp_tool, name="execute_mcp_tool"),
    path("api/navigation/counts", navigation_counts, name="navigation_counts"),
    path("api/dashboard", dashboard_summary, name="dashboard_summary"),
    # Swagger / OpenAPI docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
]
