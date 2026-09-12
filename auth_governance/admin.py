from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import AccessRequest, OAuthConfig, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("id", "username", "name", "security_groups", "is_staff")
    search_fields = ("username", "name", "email")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("MCP Nexus", {"fields": ("name", "security_groups")}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ("MCP Nexus", {"fields": ("name", "security_groups")}),
    )


@admin.register(OAuthConfig)
class OAuthConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "project", "client_id", "grant_type", "token_url", "scope")
    list_filter = ("grant_type",)
    search_fields = ("client_id", "token_url")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "tool", "status", "requested_at")
    list_filter = ("status",)
    search_fields = ("user__username", "tool__name")
