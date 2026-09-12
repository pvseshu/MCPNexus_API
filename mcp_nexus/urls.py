from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok", "service": "mcp-nexus-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
]
