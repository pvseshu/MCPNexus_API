"""Print the exact text that is sent to the embedding model (Ollama bge-large) for saved projects/tools.

Read-only: reads Postgres, does NOT call Ollama and does NOT touch Qdrant.
It reuses the same builders as embeddings/services.py, so the output is what index_application() embeds.

Run from the project root with the venv:
    python scripts/show_embedding_input.py                    # every project and its tools
    python scripts/show_embedding_input.py --app-code SPOT    # one project
    python scripts/show_embedding_input.py --tool get-an-album
    python scripts/show_embedding_input.py --limit 3          # first 3 tools per project
    python scripts/show_embedding_input.py --json             # the exact JSON body posted to Ollama /api/embed
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mcp_nexus.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402

from embeddings.services import _project_text, _tool_text  # noqa: E402
from projects.models import Project  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--app-code", help="only this project (app_code)")
    parser.add_argument("--tool", help="only the tool with this name")
    parser.add_argument("--limit", type=int, help="max tools shown per project")
    parser.add_argument("--json", action="store_true", help="print the request body sent to Ollama /api/embed")
    args = parser.parse_args()

    projects = Project.objects.all().order_by("id")
    if args.app_code:
        projects = projects.filter(app_code=args.app_code)
    if not projects:
        print("No matching projects found.")
        return 0

    for project in projects:
        tools = project.tools.all().order_by("id")
        if args.tool:
            tools = tools.filter(name=args.tool)
        if args.limit:
            tools = tools[: args.limit]
        tools = list(tools)

        # Same order as index_application(): the project text first, then one text per tool.
        texts = [_project_text(project)] + [_tool_text(t) for t in tools]

        if args.json:
            body = {"model": settings.OLLAMA_EMBEDDING_MODEL, "input": texts}
            print(f"# POST {settings.OLLAMA_URL.rstrip('/')}/api/embed   (project {project.app_code})")
            print(json.dumps(body, indent=2, ensure_ascii=False))
            continue

        print("=" * 78)
        print(f"Model: {settings.OLLAMA_EMBEDDING_MODEL}   Project: {project.name} ({project.app_code})")
        print("=" * 78)
        print("\n[PROJECT TEXT -> collection", settings.QDRANT_PROJECTS_COLLECTION, f"| point id {project.id}]")
        print(texts[0] or "(empty)")
        print(f"-- {len(texts[0])} characters")
        for tool, text in zip(tools, texts[1:]):
            print(f"\n[TOOL TEXT -> collection {settings.QDRANT_TOOLS_COLLECTION} | point id {tool.id} | {tool.name}]")
            print(text or "(empty)")
            print(f"-- {len(text)} characters")
        print(f"\nTotal texts in the embed call: {len(texts)} (1 project + {len(tools)} tools)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
