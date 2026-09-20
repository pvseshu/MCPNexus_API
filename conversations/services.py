"""Chat pipeline behind POST /api/chat/send.

Step 0: ask Llama (Ollama) whether the message is related to the application.
        If not (greeting, small talk, feelings, ...), reply directly and stop.
Step 1: otherwise load the selected application from Postgres, find matching
        tools for the message in Qdrant, and only LOG what was found.
Step 2 (next): hand the matches to the LLM and return a real answer.
"""
import json
import logging

import httpx
from django.conf import settings

from embeddings.services import search_tools
from .llm import ToolSelector
from .repository import ToolRepository
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

RELEVANCE_TIMEOUT_SECONDS = 60
RETRY_THRESHOLD_STEP = 0.05


def _join(items):
    return "; ".join(str(i) for i in items) if isinstance(items, list) else str(items or "")


def _relevance_prompt(project, message):
    ctx = project.ai_context or {}
    system = (
        "You decide whether a user's chat message is related to the application described below, "
        "i.e. a question or request the application could help with (using its use cases and workflows), "
        "or NOT related: a greeting, small talk, feelings, thanks, goodbye, or anything unrelated to the application.\n"
        "Reply with ONLY a JSON object with exactly these keys:\n"
        '  "input": the user message, copied exactly as given,\n'
        '  "response": if the message is NOT related to the application, a short, natural reply that responds '
        "to what the user actually said (return greetings, answer 'how are you', show empathy for feelings such as "
        "'feeling bad', say you're welcome to thanks, say goodbye to bye; if it is an unrelated question, politely say "
        "you can only help with this application), and then offers help with the <APP> application, mentioning its name. "
        "Do not reuse the same wording every time. Examples of the style: "
        "user 'hi' -> 'Hi there! What would you like to do in the <APP> application today?'; "
        "user 'how are you doing?' -> 'I'm doing great, thanks for asking! Is there anything I can help you with in the <APP> application?'; "
        "user 'feeling bad' -> 'I'm sorry to hear that, I hope things get better soon. If you need anything from the <APP> application, I'm happy to help.'; "
        "user 'what is the capital of France?' -> 'That's outside what I can help with, but I'm happy to help with anything in the <APP> application.'. "
        "If the message IS related to the application, use an empty string,\n"
        '  "is_app_related": true if the message is related to the application, otherwise false.\n'
        "A message that contains a real application question, even with a greeting in front of it, IS related."
    )
    system = system.replace("<APP>", project.name)
    user = (
        f"Application name: {project.name}\n"
        f"Application description: {project.description}\n"
        f"Key use cases: {_join(ctx.get('keyUseCases', []))}\n"
        f"Common workflows: {_join(ctx.get('commonWorkflows', []))}\n\n"
        f"User message: {message}"
    )
    return system, user


def check_relevance(project, message):
    """Ask Llama if `message` is about the application. Returns {"input", "response", "is_app_related"}, or None if the call failed."""
    system, user = _relevance_prompt(project, message)
    try:
        resp = httpx.post(
            f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
            json={
                "model": settings.OLLAMA_CHAT_MODEL,
                "stream": False,
                "format": "json",  # Ollama forces valid JSON output
                "options": {"temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
            timeout=RELEVANCE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        parsed = json.loads(resp.json()["message"]["content"])
        return {
            "input": message,  # always the exact user input, whatever the model echoed
            "response": str(parsed.get("response") or ""),
            # Only an explicit `false` skips the tool search; anything unclear is treated as related.
            "is_app_related": parsed.get("is_app_related") is not False,
        }
    except Exception as e:
        logger.warning("[chat] relevance check failed (%s).", e)
        return None


def no_match_reply(project, message):
    """Ask Llama for a short generic reply saying nothing in the application matched, so the user rephrases."""
    system = (
        f"You are the assistant for the {project.name} application. The user's message could not be matched "
        f"to anything the {project.name} application can do. Reply in 1-2 short, friendly sentences: say you "
        f"were unable to find {project.name}-related information for their prompt, and ask them to refine "
        "their request and try again. Do not invent any information. Reply with plain text only."
    )
    fallback = (
        f"I was unable to find {project.name} related information for your prompt. "
        "Please refine your request and try again."
    )
    try:
        resp = httpx.post(
            f"{settings.OLLAMA_URL.rstrip('/')}/api/chat",
            json={
                "model": settings.OLLAMA_CHAT_MODEL,
                "stream": False,
                "options": {"temperature": 0.4},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": message}],
            },
            timeout=RELEVANCE_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip() or fallback
    except Exception as e:
        logger.warning("[chat] no-match reply failed (%s).", e)
        return fallback


def answer(project, message):
    """Return the reply text for `message`, scoped to `project`."""
    logger.info(
        "[chat] message=%r | application: id=%s public_id=%s name=%r app_code=%r status=%s",
        message, project.id, project.public_id, project.name, project.app_code, project.status,
    )

    check = check_relevance(project, message)
    logger.info("[chat] relevance check: %s", json.dumps(check))
    if check and not check["is_app_related"]:
        return check["response"] or f"How can I help you with the {project.name} application?"

    logger.info("[chat] application description=%r ai_context=%s", project.description, project.ai_context)

    matches = search_tools(message, project.id)
    if matches == []:
        # One retry with a slightly lower bar before giving up.
        lower = round(settings.TOOL_MATCH_THRESHOLD - RETRY_THRESHOLD_STEP, 4)
        logger.info("[chat] no tool matched at %s - retrying once at %s.", settings.TOOL_MATCH_THRESHOLD, lower)
        matches = search_tools(message, project.id, threshold=lower)

    if matches is None:
        logger.warning("[chat] Qdrant/Ollama unavailable - could not search tools.")
    elif not matches:
        logger.info("[chat] no tool matched (project has %d active tools).", project.tools.filter(status="active").count())
        return no_match_reply(project, message)
    else:
        for m in matches:
            logger.info("[chat] match: score=%.3f tool_id=%s name=%s", m["score"], m["tool_id"], m["name"])
        tools = ToolRepository.get_matched_tool_details(project, matches)
        for t in tools:
            logger.info("[chat] tool details from Postgres: %s", json.dumps(t, indent=2, default=str))

        selection = ToolSelector.select_tool(project, message, tools)
        logger.info("[chat] tool selection (GPT-5.2): %s", json.dumps(selection, indent=2, default=str))
        if selection is None:
            return "Sorry, I couldn't work out which action to use right now. Please try again."
        if not selection["is_find_tool"]:
            return no_match_reply(project, message)

        # Step 5: call the selected tool's real API.
        for item in selection["tool_array"]:
            logger.info("[chat] step 5 - tool_call_result: %s", json.dumps(ToolExecutor.call(project, item), indent=2, default=str))

    return "Received your message. (Step 1: application and tool matching logged on the server.)"
