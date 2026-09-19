"""Turns an onboarded application into searchable vectors.

Why this file exists: later, when someone asks the AI agent a question like
"how do I search a customer's transactions?", the agent needs to figure out
WHICH project and WHICH tool can answer that - by comparing the meaning of
the question to the meaning of each project/tool. Comparing "meaning" is
done with embeddings: a piece of text is turned into a list of numbers (a
vector) such that texts with similar meaning end up with similar vectors.

The pipeline in index_application(), step by step:
  1. Build one plain-text summary for the project (its name, description,
     business context) and one for each selected tool (its name/description).
  2. Send all of those texts to Ollama's embedding model in one batch call ->
     get back one vector per text.
  3. Store each vector in Qdrant (a vector database) so it can later be
     searched with "find me the closest vectors to this new question".

Two external services are involved, neither of which this Django project
runs itself:
  - Ollama (OLLAMA_URL / OLLAMA_EMBEDDING_MODEL): a local server that turns
    text into vectors. Pull the model once with `ollama pull bge-large`;
    after that `ollama serve` answers HTTP requests for it - no separate
    "run" step needed.
  - Qdrant (QDRANT_URL): a database purpose-built for storing vectors and
    searching them by similarity.

If either one is down or not installed, index_application() logs a warning
and simply skips indexing (returns False) rather than failing the whole
Generate MCP Server request - saving the project/tools to Postgres always
succeeds independently of whether indexing worked.
"""
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


def _embed(texts):
    """Turn a list of plain-text strings into a list of vectors.

    Calls Ollama's HTTP API directly with `httpx` (a plain HTTP client -
    this project already uses it elsewhere for the same reason: to call an
    outside HTTP service without pulling in a dedicated SDK). One call
    embeds the whole batch at once instead of one request per text.

    Returns the list of vectors (same order as `texts`), or None if the
    call failed or came back malformed.
    """
    try:
        resp = httpx.post(
            f"{settings.OLLAMA_URL.rstrip('/')}/api/embed",
            json={"model": settings.OLLAMA_EMBEDDING_MODEL, "input": texts},
            timeout=60.0,
        )
        resp.raise_for_status()
        embeddings = resp.json().get("embeddings")
        if not embeddings or len(embeddings) != len(texts):
            logger.warning("Ollama returned an unexpected embeddings shape - skipping vector indexing.")
            return None
        return embeddings
    except Exception as e:
        logger.warning(
            "Ollama embedding call to %s failed (%s) - skipping vector indexing.",
            settings.OLLAMA_URL,
            e,
        )
        return None


def _get_qdrant_client():
    """Connect to Qdrant, or return None if it's not installed/reachable.

    qdrant-client is only imported here (not at the top of the file) so
    that this whole module can still be imported even if the package isn't
    installed - the failure only happens, harmlessly, at indexing time.
    """
    try:
        from qdrant_client import QdrantClient
    except ImportError:
        logger.warning("qdrant-client not installed - skipping vector indexing.")
        return None

    try:
        client = QdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
        client.get_collections()  # cheap call just to prove the server actually answers
        return client
    except Exception as e:
        logger.warning("Could not reach Qdrant at %s (%s) - skipping vector indexing.", settings.QDRANT_URL, e)
        return None


def _ensure_collection(client, name):
    """Create the Qdrant collection (like a SQL table, but for vectors) the
    first time it's needed. Every vector in it must have the same length
    (EMBEDDING_DIM) and the same distance metric (cosine similarity is the
    standard choice for text embeddings)."""
    from qdrant_client.models import Distance, VectorParams

    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=settings.EMBEDDING_DIM, distance=Distance.COSINE),
        )


def _project_text(project):
    """Squash everything meaningful about a project into one text blob -
    this is what actually gets embedded, so it should read like a natural
    description of what the project is for, not just a list of field names."""
    ctx = project.ai_context or {}
    parts = [
        project.name,
        project.description,
        ctx.get("businessPurpose", ""),
        ctx.get("businessDomain", ""),
        " ".join(ctx.get("keyUseCases", [])),
        " ".join(ctx.get("commonWorkflows", [])),
        " ".join(f"{t.get('term', '')}: {t.get('definition', '')}" for t in ctx.get("importantTerminology", [])),
        " ".join(ctx.get("intendedConsumers", [])),
        ctx.get("usageGuidelines", ""),
        ctx.get("restrictions", ""),
        ctx.get("aiGuidance", ""),
    ]
    return "\n".join(p for p in parts if p)


def _tool_text(tool):
    """Same idea as _project_text, but for one MCP tool."""
    parts = [tool.name, tool.display_name, tool.summary, tool.description]
    return "\n".join(p for p in parts if p)


def index_application(project, tools):
    """Embed the project + its tools and store the vectors in Qdrant.

    Called once, at the end of the Generate MCP Server step. Returns True if
    indexing actually happened, False if it was skipped (Ollama/Qdrant not
    reachable, or qdrant-client not installed) - callers should treat False
    as "not fatal, just not searchable yet", never as an error to raise.
    """
    client = _get_qdrant_client()
    if client is None:
        return False

    from qdrant_client.models import PointStruct

    # One embedding call for everything: [project_text, tool1_text, tool2_text, ...]
    texts = [_project_text(project)] + [_tool_text(t) for t in tools]
    vectors = _embed(texts)
    if vectors is None:
        return False

    project_vector, tool_vectors = vectors[0], vectors[1:]

    _ensure_collection(client, settings.QDRANT_PROJECTS_COLLECTION)
    _ensure_collection(client, settings.QDRANT_TOOLS_COLLECTION)

    # "payload" is Qdrant's term for the plain data attached to a vector -
    # it's what you get back alongside the match when you search later.
    client.upsert(
        collection_name=settings.QDRANT_PROJECTS_COLLECTION,
        points=[
            PointStruct(
                id=project.id,
                vector=project_vector,
                payload={"project_id": project.id, "name": project.name, "app_code": project.app_code},
            )
        ],
    )

    if tools:
        client.upsert(
            collection_name=settings.QDRANT_TOOLS_COLLECTION,
            points=[
                PointStruct(
                    id=tool.id,
                    vector=vector,
                    payload={
                        "tool_id": tool.id,
                        "project_id": project.id,
                        "name": tool.name,
                        "required_permission": tool.required_permission,
                    },
                )
                for tool, vector in zip(tools, tool_vectors)
            ],
        )

    logger.info("Indexed project %s + %d tool(s) into Qdrant.", project.id, len(tools))
    return True


def reindex_project(project):
    """Re-embed just the project text, e.g. after its AI context was edited.

    Best effort like index_application: returns False if Ollama/Qdrant is
    unavailable, so the caller's save is never blocked by it.
    """
    client = _get_qdrant_client()
    if client is None:
        return False

    from qdrant_client.models import PointStruct

    vectors = _embed([_project_text(project)])
    if vectors is None:
        return False

    _ensure_collection(client, settings.QDRANT_PROJECTS_COLLECTION)
    client.upsert(
        collection_name=settings.QDRANT_PROJECTS_COLLECTION,
        points=[
            PointStruct(
                id=project.id,
                vector=vectors[0],
                payload={"project_id": project.id, "name": project.name, "app_code": project.app_code},
            )
        ],
    )
    logger.info("Re-indexed project %s into Qdrant.", project.id)
    return True
