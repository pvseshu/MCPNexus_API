import secrets
import string

from django.db import models
from django.utils.text import slugify

_PUBLIC_ID_ALPHABET = string.ascii_lowercase + string.digits


def generate_public_id(base):
    """Public id like 'app-spot-x7k2m9': a slug of `base` plus a random suffix (not derived from any counter)."""
    slug = slugify(base)[:60] or "app"
    suffix = "".join(secrets.choice(_PUBLIC_ID_ALPHABET) for _ in range(6))
    return f"app-{slug}-{suffix}"


class Project(models.Model):
    """An onboarded enterprise application (e.g. Spotify)."""

    STATUS_CHOICES = [
        ("pending", "pending"),
        ("active", "active"),
        ("failed", "failed"),
        ("disabled", "disabled"),
        ("maintenance", "maintenance"),
    ]

    # Opaque external identifier for embeds / other systems (the integer id stays internal).
    # Generated once when the project is first saved and never changed afterwards.
    public_id = models.CharField(max_length=100, unique=True, editable=False)
    name = models.CharField(max_length=200, unique=True)
    description = models.TextField(blank=True)
    openapi_url = models.URLField(max_length=1000, blank=True)
    base_url = models.URLField(max_length=1000, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")

    # --- App Registration wizard fields (see API.md #2 Generate MCP Server) ---
    app_code = models.CharField(max_length=100, blank=True, default="", db_index=True)
    car_id = models.CharField(max_length=100, blank=True, default="")
    owner = models.CharField(max_length=200, blank=True, default="")
    owner_email = models.EmailField(blank=True, default="")
    support_dl = models.EmailField(blank=True, default="")
    department = models.CharField(max_length=200, blank=True, default="")
    swagger_urls = models.JSONField(default=list, blank=True)
    # Auth config for reaching the app's own OpenAPI/runtime endpoints. Secrets
    # (e.g. servicePassword) are stripped before saving - see
    # projects.views._sanitize_auth_config.
    auth_config = models.JSONField(default=dict, blank=True)
    # Freeform business context fed to the AI agent (businessPurpose, keyUseCases,
    # importantTerminology, aiGuidance, ...). See API.md aiContext shape.
    ai_context = models.JSONField(default=dict, blank=True)
    is_ai_ready = models.BooleanField(default=False)
    # AI summary settings from the detail modal; null until someone configures it.
    ai_summary_config = models.JSONField(null=True, blank=True)

    # --- MCP server (one per project, created on Generate MCP Server) ---
    mcp_endpoint_url = models.URLField(max_length=500, blank=True, default="")
    mcp_version = models.CharField(max_length=20, default="1.0.0", blank=True)
    mcp_transport_type = models.CharField(max_length=50, default="Streamable HTTP", blank=True)
    mcp_health_status = models.CharField(max_length=20, default="Unknown", blank=True)
    is_published_to_catalog = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.public_id:
            base = self.app_code or self.name
            for _ in range(10):  # retry on the (very unlikely) suffix collision
                candidate = generate_public_id(base)
                if not Project.objects.filter(public_id=candidate).exists():
                    self.public_id = candidate
                    break
            else:
                raise RuntimeError("Could not generate a unique public_id")
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name
