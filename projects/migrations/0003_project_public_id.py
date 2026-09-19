import secrets
import string

from django.db import migrations, models
from django.utils.text import slugify


def backfill_public_ids(apps, schema_editor):
    Project = apps.get_model("projects", "Project")
    alphabet = string.ascii_lowercase + string.digits
    for project in Project.objects.filter(public_id__isnull=True):
        slug = slugify(project.app_code or project.name)[:60] or "app"
        while True:
            candidate = f"app-{slug}-{''.join(secrets.choice(alphabet) for _ in range(6))}"
            if not Project.objects.filter(public_id=candidate).exists():
                break
        project.public_id = candidate
        project.save(update_fields=["public_id"])


class Migration(migrations.Migration):

    dependencies = [
        ("projects", "0002_project_ai_context_project_app_code_and_more"),
    ]

    operations = [
        # 1) add as nullable so existing rows are allowed, 2) fill them, 3) make it required + unique.
        migrations.AddField(
            model_name="project",
            name="public_id",
            field=models.CharField(max_length=100, null=True, editable=False),
        ),
        migrations.RunPython(backfill_public_ids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="project",
            name="public_id",
            field=models.CharField(max_length=100, unique=True, editable=False),
        ),
    ]
