"""Chat pipeline behind POST /api/chat/send.

Step 0: ask Llama (Ollama) whether the message is related to the application.
        If not (greeting, small talk, feelings, ...), reply directly and stop.
Step 1: otherwise load the selected application from Postgres, find matching
        tools for the message in Qdrant, and only LOG what was found.
Step 2 (next): hand the matches to the LLM and return a real answer.
"""
import json
import logging
import secrets
import string

from django.conf import settings

from embeddings.services import search_tools
from .llama import check_relevance, no_match_reply
from .llm import ResponseFormatter, ToolSelector
from .repository import ConversationRepository, ToolRepository
from .tool_executor import ToolExecutor

logger = logging.getLogger(__name__)

RETRY_THRESHOLD_STEP = 0.05
MAX_RETRIES = 1
MIN_MATCHES = 4
SESSION_ID_LENGTH = 10
PREVIOUS_API_TURNS = 3  # earlier successful API turns of the session passed to step 4
RESPONSE_PREVIEW_CHARS = 8000  # how much of an API response is kept in the conversation log

# TODO: Shrink the response before we send to model, to avoid hitting the model's max context length. The model can handle 120k chars, but we don't want to send that much. We can summarize or truncate the response before sending it to the model.

_SESSION_ID_ALPHABET = string.ascii_letters + string.digits


def _reply(message, status="success", items=None):
    """The chat response body: the text, a status, and the list (empty unless the answer has one)."""
    return {"status": status, "message": message, "list": items or []}


def new_session_id():
    """A random 10-character id for a new chat window, e.g. 'k3Xr9QmZ2a'."""
    return "".join(secrets.choice(_SESSION_ID_ALPHABET) for _ in range(SESSION_ID_LENGTH))


def _summarize_result(result):
    """The parts of one API call worth keeping in the log (the response itself is cut short)."""
    preview = json.dumps(result.get("response"), default=str) if result.get("response") is not None else ""
    return {
        "success": result.get("success"),
        "httpStatus": result.get("httpStatus"),
        "durationMs": result.get("durationMs"),
        "request": result.get("request"),
        "error": result.get("error"),
        "responsePreview": preview[:RESPONSE_PREVIEW_CHARS],
    }


def answer(project, message, session_id=None, user=None):
    """Run one chat turn and save it. Returns {"sessionId", "status", "message", "list"}.
    `session_id` ties the turns of one chat window together; a new one is created when it is empty.
    """
    session_id = session_id or new_session_id()
    trace = {"outcome": "", "tools_matched": [], "tools_called": [], "parameters_used": {}, "result_summary": {}}

    reply = _run(project, message, session_id, trace)

    try:
        ConversationRepository.save_turn(user, session_id, project, message, reply, trace)
    except Exception:
        # Never lose the answer because the turn could not be saved.
        logger.exception("[chat] could not save the conversation log.")
    return {"sessionId": session_id, **reply}


def _run(project, message, session_id, trace):
    """The chat pipeline for one message; fills `trace` with what happened and returns the reply dict."""
    logger.info(
        "[chat] message=%r | application: id=%s public_id=%s name=%r app_code=%r status=%s",
        message, project.id, project.public_id, project.name, project.app_code, project.status,
    )

    # Step 1: Llama checks the message is about the application (otherwise reply directly and stop).
    check = check_relevance(project, message)
    logger.info("[chat] step 1 - relevance check: %s", json.dumps(check))
    if check and not check["is_app_related"]:
        trace["outcome"] = "not_app_related"
        return _reply(check["response"] or f"How can I help you with the {project.name} application?")

    logger.info("[chat] application description=%r ai_context=%s", project.description, project.ai_context)

    # Step 2: search Qdrant for matching tools, retrying at a lower threshold while there are too few.
    matches = search_tools(message, project.id)
    threshold = settings.TOOL_MATCH_THRESHOLD
    for _ in range(MAX_RETRIES):
        if matches and len(matches) >= MIN_MATCHES:
            break
        # None (search failed), empty, or too few matches: retry with a lower bar (e.g. .60 -> .55 -> .50 -> .45).
        lower = round(threshold - RETRY_THRESHOLD_STEP, 4)
        logger.info("[chat] only %d tool(s) matched at %s - retrying at %s.", len(matches or []), threshold, lower)
        retry = search_tools(message, project.id, threshold=lower)
        threshold = lower
        if retry is not None:
            matches = retry

    trace["tools_matched"] = matches or []

    if matches is None:
        logger.warning("[chat] Qdrant/Ollama unavailable - could not search tools.")
        trace["outcome"] = "no_match"
    elif not matches:
        trace["outcome"] = "no_match"
        logger.info("[chat] no tool matched (project has %d active tools).", project.tools.filter(status="active").count())
        return _reply(no_match_reply(project, message))
    else:
        for m in matches:
            logger.info("[chat] step 2 - match: score=%.3f tool_id=%s name=%s", m["score"], m["tool_id"], m["name"])

        # Step 3: load the full details (with parameters) of the matched tools from Postgres.
        tools = ToolRepository.get_matched_tool_details(project, matches)
        for t in tools:
            logger.info("[chat] step 3 - tool details from Postgres: %s", json.dumps(t, indent=2, default=str))

        # Step 4: GPT-5.2 picks the tool(s) that answer the question and fills in their parameters.
        # It also gets the last successful API turns of this chat window, so a parameter the user only
        # implies ("the second one") can be taken from an earlier API response.
        previous = ConversationRepository.get_recent_api_turns(session_id, project, PREVIOUS_API_TURNS)
        selection = ToolSelector.select_tool(project, message, tools, previous)
        logger.info("[chat] step 4 - tool selection (GPT-5.2): %s", json.dumps(selection, indent=2, default=str))
        if selection is None:
            trace["outcome"] = "selection_failed"
            return _reply("Sorry, I couldn't work out which action to use right now. Please try again.", "error")
        if not selection["is_find_tool"]:
            trace["outcome"] = "no_match"
            return _reply(no_match_reply(project, message))
        trace["tools_called"] = [i["tool_name"] for i in selection["tool_array"]]
        trace["parameters_used"] = {i["tool_name"]: i["parameters"] for i in selection["tool_array"]}

        # Step 5: call the selected tool's real API.
        tool_results = []
        for item in selection["tool_array"]:
            result = ToolExecutor.call(project, item)
            logger.info("[chat] step 5 - tool_call_result: %s", json.dumps(result, indent=2, default=str))
            tool_results.append({"tool_name": item["tool_name"], "result": result})
            trace["result_summary"][item["tool_name"]] = _summarize_result(result)

        failed = [r for r in tool_results if not r["result"]["success"]]
        if failed:
            trace["outcome"] = "api_error"
            return _reply(f"Sorry, I couldn't get that from {project.name}: {failed[0]['result']['error']}", "error")

        # Step 6: GPT-5.2 picks the needed data out of the response and words the answer.
        formatted = ResponseFormatter.format_answer(project, message, tool_results)
        logger.info("[chat] step 6 - formatted answer: %s", json.dumps(formatted, indent=2, default=str))
        if formatted is None or not formatted["output"]:
            trace["outcome"] = "format_failed"
            return _reply("Sorry, I got the data but couldn't put together an answer. Please try again.", "error")
        trace["outcome"] = "answered"
        return _reply(formatted["output"], items=formatted["is_list"])

    return _reply("Received your message. (Step 1: application and tool matching logged on the server.)")
