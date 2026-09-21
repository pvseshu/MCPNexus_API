from django.contrib import admin

from .models import ConversationLog


@admin.register(ConversationLog)
class ConversationLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session_id",
        "project_name",
        "question",
        "outcome",
        "api_called",
        "created_at",
    )
    list_filter = ("matched_project", "outcome", "api_called", "created_at")
    search_fields = ("question", "final_answer", "session_id")
    readonly_fields = ("created_at",)
