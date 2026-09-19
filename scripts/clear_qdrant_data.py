"""Delete all points from Qdrant collections, keeping the collections themselves
(vector config, indexes) intact.

Standalone: reads QDRANT_* settings from .env, no Django needed.
Requires: qdrant-client, python-dotenv.

Usage:
    python scripts/clear_qdrant_data.py          # the two configured project/tool collections
    python scripts/clear_qdrant_data.py --all    # every collection on the server
    python scripts/clear_qdrant_data.py --yes    # no confirmation prompt
"""
import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient, models


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--all", action="store_true", help="clear every collection, not just the configured ones")
    args = parser.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    client = QdrantClient(url=url, api_key=os.getenv("QDRANT_API_KEY") or None)

    existing = {c.name for c in client.get_collections().collections}
    if args.all:
        targets = sorted(existing)
    else:
        wanted = [
            os.getenv("QDRANT_PROJECTS_COLLECTION", "mcp_projects"),
            os.getenv("QDRANT_TOOLS_COLLECTION", "mcp_tools"),
        ]
        targets = [c for c in wanted if c in existing]
    if not targets:
        print("No matching collections found.")
        return 0

    print(f"Qdrant: {url}")
    print("Will delete ALL points from:")
    for name in targets:
        print(f"  - {name} ({client.count(name, exact=True).count} points)")
    if not args.yes and input("Type 'yes' to continue: ").strip().lower() != "yes":
        print("Aborted.")
        return 1

    for name in targets:
        # An empty filter matches every point.
        client.delete(
            collection_name=name,
            points_selector=models.FilterSelector(filter=models.Filter()),
            wait=True,
        )
        print(f"Cleared {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
