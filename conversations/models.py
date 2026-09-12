from django.conf import settings
from django.db import models


class ConversationLog(models.Model):
    """One chat turn: what was asked, what was called, what was answered."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversation_logs",
    )
    session_id = models.CharField(max_length=100, blank=True, db_index=True)
    question = models.TextField()
    matched_project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversation_logs",
    )
    tools_called = models.JSONField(default=list, blank=True)
    parameters_used = models.JSONField(default=dict, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    final_answer = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user}: {self.question[:60]}"
