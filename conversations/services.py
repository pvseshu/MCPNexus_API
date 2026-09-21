"""Chat pipeline behind POST /api/chat/send.

Step 0: ask Llama (Ollama) whether the message is related to the application.
        If not (greeting, small talk, feelings, ...), reply directly and stop.
Step 1: otherwise load the selected application from Postgres, find matching
        tools for the message in Qdrant, and only LOG what was found.
Step 2 (next): hand the matches to the LLM and return a real answer.
"""
import json
import logging

from django.conf import settings

from embeddings.services import search_tools
from .llama import check_relevance, no_match_reply
from .llm import ResponseFormatter, ToolSelector
from .repository import ToolRepository
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

RETRY_THRESHOLD_STEP = 0.05


def _reply(message, status="success", items=None):
    """The chat response body: the text, a status, and the list (empty unless the answer has one)."""
    return {"status": status, "message": message, "list": items or []}


def answer(project, message):
    """Return the chat response dict {"status", "message", "list"} for `message`, scoped to `project`."""
    logger.info(
        "[chat] message=%r | application: id=%s public_id=%s name=%r app_code=%r status=%s",
        message, project.id, project.public_id, project.name, project.app_code, project.status,
    )

    check = check_relevance(project, message)
    logger.info("[chat] relevance check: %s", json.dumps(check))
    if check and not check["is_app_related"]:
        return _reply(check["response"] or f"How can I help you with the {project.name} application?")

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
        return _reply(no_match_reply(project, message))
    else:
        for m in matches:
            logger.info("[chat] match: score=%.3f tool_id=%s name=%s", m["score"], m["tool_id"], m["name"])
        tools = ToolRepository.get_matched_tool_details(project, matches)
        for t in tools:
            logger.info("[chat] tool details from Postgres: %s", json.dumps(t, indent=2, default=str))

        selection = ToolSelector.select_tool(project, message, tools)
        logger.info("[chat] tool selection (GPT-5.2): %s", json.dumps(selection, indent=2, default=str))
        if selection is None:
            return _reply("Sorry, I couldn't work out which action to use right now. Please try again.", "error")
        if not selection["is_find_tool"]:
            return _reply(no_match_reply(project, message))

        # Step 5: call the selected tool's real API.
        tool_results = []
        for item in selection["tool_array"]:
            result = ToolExecutor.call(project, item)
            logger.info("[chat] step 5 - tool_call_result: %s", json.dumps(result, indent=2, default=str))
            tool_results.append({"tool_name": item["tool_name"], "result": result})

        failed = [r for r in tool_results if not r["result"]["success"]]
        if failed:
            return _reply(f"Sorry, I couldn't get that from {project.name}: {failed[0]['result']['error']}", "error")

        # Step 6: GPT-5.2 picks the needed data out of the response and words the answer.
        formatted = ResponseFormatter.format_answer(project, message, tool_results)
        logger.info("[chat] step 6 - formatted answer: %s", json.dumps(formatted, indent=2, default=str))
        if formatted is None or not formatted["output"]:
            return _reply("Sorry, I got the data but couldn't put together an answer. Please try again.", "error")
        return _reply(formatted["output"], items=formatted["is_list"])

    return _reply("Received your message. (Step 1: application and tool matching logged on the server.)")
