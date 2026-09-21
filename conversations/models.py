from django.conf import settings
from django.db import models


class ConversationLog(models.Model):
    """One chat turn: what was asked, whether an API was called, and what was answered.

    All turns of one chat window share the same `session_id`; that is how earlier
    questions and answers are found again for the next question.
    """

    OUTCOME_CHOICES = [
        ("answered", "Answered from an API call"),
        ("not_app_related", "Not about the application (greeting, small talk, ...)"),
        ("no_match", "No tool matched the question"),
        ("selection_failed", "The LLM could not pick a tool"),
        ("api_error", "The tool's API call failed"),
        ("format_failed", "API data came back but no answer could be written"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversation_logs",
    )
    session_id = models.CharField(max_length=100, blank=True, db_index=True)
    matched_project = models.ForeignKey(
        "projects.Project",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="conversation_logs",
    )
    project_name = models.CharField(max_length=255, blank=True)  # copy, so history stays readable if the app is renamed/deleted

    question = models.TextField()
    final_answer = models.TextField(blank=True)
    answer_items = models.JSONField(default=list, blank=True)  # the "list" part of the reply
    status = models.CharField(max_length=20, default="success")  # success | error
    outcome = models.CharField(max_length=30, choices=OUTCOME_CHOICES, blank=True)

    # Was a real API called for this turn?
    api_called = models.BooleanField(default=False)
    tools_matched = models.JSONField(default=list, blank=True)  # Qdrant candidates: [{tool_id, name, score}]
    tools_called = models.JSONField(default=list, blank=True)  # tool names the LLM chose
    parameters_used = models.JSONField(default=dict, blank=True)  # {tool_name: parameters}
    result_summary = models.JSONField(default=dict, blank=True)  # {tool_name: {success, httpStatus, durationMs, request, error, responsePreview}}

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.session_id[:8]}: {self.question[:60]}"
