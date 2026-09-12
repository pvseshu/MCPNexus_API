from django.contrib import admin

from .models import Tool, ToolParameter


class ToolParameterInline(admin.TabularInline):
    model = ToolParameter
    extra = 0


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "project",
        "api",
        "http_method",
        "path",
        "status",
    )
    list_filter = ("project", "http_method", "status")
    search_fields = ("name", "description", "summary", "operation_id", "path")
    inlines = [ToolParameterInline]


@admin.register(ToolParameter)
class ToolParameterAdmin(admin.ModelAdmin):
    list_display = ("id", "tool", "name", "location", "data_type", "required")
    list_filter = ("location", "required", "data_type")
    search_fields = ("name", "description")
