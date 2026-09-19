from django.db import models


class Api(models.Model):
    """One API/service record discovered inside a project OpenAPI spec."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="apis"
    )
    name = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    version = models.CharField(max_length=50, blank=True)
    openapi_version = models.CharField(max_length=50, blank=True)
    # URL the OpenAPI/Swagger spec was downloaded from.
    spec_url = models.URLField(max_length=1000, blank=True)
    # Server the endpoints are called on (from the spec's servers / host+basePath).
    base_url = models.URLField(max_length=1000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project_id", "name"]
        unique_together = [("project", "name")]
        verbose_name = "API"
        verbose_name_plural = "APIs"

    def __str__(self):
        return f"{self.project.name} / {self.name}"
