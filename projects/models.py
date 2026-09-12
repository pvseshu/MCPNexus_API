from django.db import models


class Project(models.Model):
    """An onboarded enterprise application (e.g. Spotify)."""

    STATUS_CHOICES = [
        ("pending", "pending"),
        ("active", "active"),
        ("failed", "failed"),
        ("disabled", "disabled"),
    ]

    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    openapi_url = models.URLField(max_length=1000, blank=True)
    base_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
