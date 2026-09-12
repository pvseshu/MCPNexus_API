from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Mock demo user: a normal Django user plus the security groups the
    governance layer checks tool access against."""

    name = models.CharField(max_length=200, blank=True)
    security_groups = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name or self.username

    def has_groups(self, required_groups):
        """True if the user holds every group the tool requires."""
        required = set(required_groups or [])
        if not required:
            return True
        return required.issubset(set(self.security_groups or []))

    def missing_groups(self, required_groups):
        return sorted(set(required_groups or []) - set(self.security_groups or []))


class OAuthConfig(models.Model):
    GRANT_CHOICES = [
        ("client_credentials", "client_credentials"),
        ("authorization_code", "authorization_code"),
    ]

    project = models.OneToOneField(
        "projects.Project", on_delete=models.CASCADE, related_name="oauth_config"
    )
    client_id = models.CharField(max_length=255)
    client_secret = models.CharField(max_length=255)
    redirect_uri = models.CharField(max_length=500, blank=True)
    token_url = models.URLField(max_length=500)
    scope = models.CharField(max_length=500, blank=True)
    grant_type = models.CharField(
        max_length=50, choices=GRANT_CHOICES, default="client_credentials"
    )
    # Cached token so every tool call does not re-authenticate.
    access_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"OAuth<{self.project.name}>"


class AccessRequest(models.Model):
    STATUS_CHOICES = [
        ("pending", "pending"),
        ("approved", "approved"),
        ("rejected", "rejected"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="access_requests",
    )
    tool = models.ForeignKey(
        "tools.Tool", on_delete=models.CASCADE, related_name="access_requests"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    reason = models.TextField(blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user} -> {self.tool} [{self.status}]"
