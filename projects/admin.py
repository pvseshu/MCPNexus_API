from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("id", "public_id", "name", "status", "base_url", "created_at")
    list_filter = ("status",)
    search_fields = ("public_id", "name", "description", "openapi_url")
    readonly_fields = ("public_id",)
