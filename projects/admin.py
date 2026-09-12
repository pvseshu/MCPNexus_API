from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "status", "base_url", "created_at")
    list_filter = ("status",)
    search_fields = ("name", "description", "openapi_url")
