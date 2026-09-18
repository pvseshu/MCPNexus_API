from django.db import models

HTTP_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]


class Tool(models.Model):
    """A single callable operation from an API, exposed as an MCP tool."""

    STATUS_CHOICES = [
        ("active", "active"),
        ("disabled", "disabled"),
    ]
    METHOD_CHOICES = [(m, m) for m in HTTP_METHODS]

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="tools"
    )
    api = models.ForeignKey(
        "api_registry.Api", on_delete=models.CASCADE, related_name="tools"
    )
    name = models.CharField(max_length=300)
    display_name = models.CharField(max_length=300, blank=True, default="")
    description = models.TextField(blank=True)
    http_method = models.CharField(max_length=10, choices=METHOD_CHOICES, default="GET")
    path = models.CharField(max_length=1000)
    operation_id = models.CharField(max_length=300, blank=True)
    summary = models.TextField(blank=True)
    tags = models.JSONField(default=list, blank=True)
    request_schema = models.JSONField(default=dict, blank=True)
    response_schema = models.JSONField(default=dict, blank=True)
    required_security_groups = models.JSONField(default=list, blank=True)
    # Permission string the governance layer checks (e.g. "MCP_GETCUSTOMER").
    required_permission = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project_id", "name"]
        unique_together = [("project", "name")]

    def __str__(self):
        return f"{self.project.name}.{self.name}"

    @property
    def endpoint(self):
        return f"{self.http_method} {self.path}"


class ToolParameter(models.Model):
    LOCATION_CHOICES = [
        ("query", "query"),
        ("path", "path"),
        ("body", "body"),
        ("header", "header"),
        ("cookie", "cookie"),
    ]

    tool = models.ForeignKey(Tool, on_delete=models.CASCADE, related_name="parameters")
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=20, choices=LOCATION_CHOICES, default="query")
    data_type = models.CharField(max_length=50, default="string")
    required = models.BooleanField(default=False)
    description = models.TextField(blank=True)
    default_value = models.CharField(max_length=500, blank=True)
    enum_values = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["tool_id", "-required", "name"]
        unique_together = [("tool", "name", "location")]

    def __str__(self):
        return f"{self.tool.name}.{self.name} ({self.location})"
