"""OpenAI (GPT-5.2) calls for the chat pipeline, over plain HTTP like scripts/ask_gpt.py."""
import json
import logging

import httpx
from django.conf import settings

from .llama import _join

logger = logging.getLogger(__name__)

TOOL_SELECT_TIMEOUT_SECONDS = 90
FORMAT_TIMEOUT_SECONDS = 120
# GPT-5.2 has a large context window; this only stops a huge API response from being sent whole.
MAX_RESULT_CHARS = 120000

_SELECT_SYSTEM_PROMPT = """You are the tool router for the "{app}" application.
You get the user's exact question and a JSON list of candidate API tools (name, description, when_to_use, \
when_not_to_use, call_sequence, parameters, sample_inputs, ...).
Pick the tool(s) that exactly answer the question and fill in their parameters from the question.

Rules:
- Only use tools from the list. Never invent a tool or a parameter.
- Parameter names must match the tool's own parameter names. Only set values the user actually gave or that \
are clearly implied; leave out the others (never guess a value).
- If no tool in the list fits the question, set is_find_tool to false and tool_array to [].
- Usually exactly one tool fits; return more than one only if the question truly needs several.

Reply with ONLY a JSON object with exactly these keys:
{{
  "input": the user's question, copied exactly,
  "is_find_tool": true or false,
  "tool_array": [ {{ "tool_name": "<exact tool name from the list>", "parameters": {{ "<parameter name>": <value> }} }} ]
}}"""

_FORMAT_SYSTEM_PROMPT = """You answer a user's question about the "{app}" application using the data returned by its API.
You get the application details, the user's exact question and the API response(s), which may be very large.
Pick out only the data that is really needed to answer the question; ignore everything else.
Use only the API data. Never make up values. If the data does not contain the answer, say so.

LIST RULE: if the answer is 2 or more items (names, titles, ids, ...), you MUST put every item in "is_list" and you must NOT write the items in "output". Only a single value or a yes/no/count answer stays in "output". Questions like "what are my ...", "show ...", "list ..." ask for the items themselves, not for a count.

Reply with ONLY a JSON object with exactly these keys:
{{
  "input": the user's question, copied exactly,
  "output": the answer in clear plain text. When "is_list" has items, "output" is ONLY a short generic sentence that introduces them (no item names) followed by a relevant follow-up question the user might want next. Otherwise "output" is the direct answer,
  "is_list": array of short strings with the items, or [] when the answer is not a list
}}

Examples (the data and wording here are only illustrations, write your own wording for the real question):
question: "show my items" / data has items A and B ->
{{"input": "show my items", "output": "These are your items. Do you want details about any of them?", "is_list": ["A", "B"]}}
question: "how many items do I have?" / data has 2 items ->
{{"input": "how many items do I have?", "output": "You have 2 items.", "is_list": []}}"""


class ToolSelector:
    """Asks GPT-5.2 (OpenAI) which of the candidate tools answers the user's question."""

    @staticmethod
    def select_tool(project, message, tools):
        """Returns {"input", "is_find_tool", "tool_array"}, or None if the LLM call failed / is not configured."""
        if not settings.OPENAI_API_KEY:
            logger.warning("[chat] OPENAI_API_KEY is not set.")
            return None
        try:
            resp = httpx.post(
                f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": settings.OPENAI_MODEL,
                    "reasoning_effort": settings.OPENAI_REASONING_EFFORT,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _SELECT_SYSTEM_PROMPT.format(app=project.name)},
                        {
                            "role": "user",
                            "content": f"User question: {message}\n\nCandidate tools:\n{json.dumps(tools, default=str)}",
                        },
                    ],
                },
                timeout=TOOL_SELECT_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
        except Exception as e:
            logger.warning("[chat] tool selection call failed (%s).", e)
            return None

        known = {t["name"] for t in tools}
        tool_array = [
            {"tool_name": t["tool_name"], "parameters": t.get("parameters") or {}}
            for t in parsed.get("tool_array") or []
            if isinstance(t, dict) and t.get("tool_name") in known  # drop anything the model invented
        ]
        return {
            "input": message,  # always the exact user question
            "is_find_tool": bool(tool_array) and parsed.get("is_find_tool") is True,
            "tool_array": tool_array,
        }


class ResponseFormatter:
    """Asks GPT-5.2 to turn the raw tool response into a user-facing answer."""

    @staticmethod
    def format_answer(project, message, tool_results):
        """`tool_results` is a list of {"tool_name", "result"}.

        Returns {"input", "output", "is_list"}, or None if the call failed / is not configured.
        """
        if not settings.OPENAI_API_KEY:
            logger.warning("[chat] OPENAI_API_KEY is not set.")
            return None

        ctx = project.ai_context or {}
        data = json.dumps(tool_results, default=str)
        truncated = len(data) > MAX_RESULT_CHARS
        if truncated:
            data = data[:MAX_RESULT_CHARS]
            logger.info("[chat] tool response is large - sending the first %d characters to GPT.", MAX_RESULT_CHARS)

        user = (
            f"Application name: {project.name}\n"
            f"Application description: {project.description}\n"
            f"Key use cases: {_join(ctx.get('keyUseCases', []))}\n"
            f"Common workflows: {_join(ctx.get('commonWorkflows', []))}\n\n"
            f"User question: {message}\n\n"
            f"API response{' (cut off, it was too large)' if truncated else ''}:\n{data}"
        )
        try:
            resp = httpx.post(
                f"{settings.OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": settings.OPENAI_MODEL,
                    "reasoning_effort": settings.OPENAI_REASONING_EFFORT,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": _FORMAT_SYSTEM_PROMPT.format(app=project.name)},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=FORMAT_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            parsed = json.loads(resp.json()["choices"][0]["message"]["content"])
        except Exception as e:
            logger.warning("[chat] response formatting failed (%s).", e)
            return None

        is_list = parsed.get("is_list")
        return {
            "input": message,  # always the exact user question
            "output": str(parsed.get("output") or ""),
            "is_list": [str(i) for i in is_list] if isinstance(is_list, list) else [],
        }
