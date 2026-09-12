from django.contrib import admin

from .models import ConversationLog


@admin.register(ConversationLog)
class ConversationLogAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "session_id",
        "matched_project",
        "question",
        "created_at",
    )
    list_filter = ("matched_project", "created_at")
    search_fields = ("question", "final_answer", "session_id")
    readonly_fields = ("created_at",)
