from django.contrib import admin

from .models import Api


@admin.register(Api)
class ApiAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "project", "version", "openapi_version", "base_url")
    list_filter = ("project",)
    search_fields = ("name", "description")
