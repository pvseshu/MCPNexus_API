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
MAX_RESULT_CHARS = 20000

_SELECT_SYSTEM_PROMPT = """You are the tool router for the "{app}" application.
You get the user's exact question and a JSON list of candidate API tools (name, description, when_to_use, \
when_not_to_use, call_sequence, parameters, sample_inputs, ...).
Pick the tool(s) that exactly answer the question and fill in their parameters from the question.

Rules:
- Only use tools from the list. Never invent a tool or a parameter.
- Parameter names must match the tool's own parameter names. Only set values the user actually gave or that \
are clearly implied; leave out the others (never guess a value).
- If no tool in the list fits the question, set is_find_tool to false and tool_array to [].
- You may also get the previous questions and answers of this chat, each with the tool(s) that were called and \
the API response JSON. If a tool needs an input parameter (in the URI/path, the query string or the input JSON) \
that the current question does not give but an earlier question, answer or API response does (for example an id \
for "the second one" or "that item"), map that value from the earlier data into the parameters. Use only values \
that really appear there; never guess.
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

FIELD MAPPING RULE: the API response is usually far larger than the answer, but a few of its values \
(ids, keys, codes, references) are what a later API call would need as an input parameter. Put only those \
values in "fieldMapping" and drop everything else (labels, timestamps, descriptions, display text, nested \
payloads). Key it by the item as it appears in "is_list" so the values of one item can be found again; when \
the answer is not a list, key it by the field name. Copy the values exactly as the API returned them, never \
invent one, and use {{}} when the response has nothing an API could take as input.

Reply with ONLY a JSON object with exactly these keys:
{{
  "input": the user's question, copied exactly,
  "output": the answer in clear plain text. When "is_list" has items, "output" is ONLY a short generic sentence that introduces them (no item names) followed by a relevant follow-up question the user might want next. Otherwise "output" is the direct answer,
  "is_list": array of short strings with the items, or [] when the answer is not a list,
  "fieldMapping": JSON object with the few response values a later API call could use as input, or {{}}
}}

Examples (the data and wording here are only illustrations, write your own wording for the real question):
question: "show my items" / data has items A (id 101, code AA-1) and B (id 102, code BB-7) ->
{{"input": "show my items", "output": "These are your items. Do you want details about any of them?", "is_list": ["A", "B"], "fieldMapping": {{"A": {{"id": 101, "code": "AA-1"}}, "B": {{"id": 102, "code": "BB-7"}}}}}}
question: "how many items do I have?" / data has 2 items (id 101 and 102) ->
{{"input": "how many items do I have?", "output": "You have 2 items.", "is_list": [], "fieldMapping": {{"ids": [101, 102]}}}}"""


def _previous_turns_text(previous):
    """The earlier questions, answers and API response JSON as prompt text, or "" when there are none."""
    if not previous:
        return ""
    blocks = []
    for i, turn in enumerate(previous, 1):
        responses = "\n".join(
            f"API response of tool {name}: {text}" for name, text in turn["api_responses"].items()
        )
        blocks.append(
            f"[{i}] Question: {turn['question']}\n"
            f"Answer: {turn['answer']}\n"
            f"Tools called: {', '.join(turn['tools_called'])}\n{responses}"
        )
    return "Previous questions and answers of this chat (oldest first):\n" + "\n\n".join(blocks) + "\n\n"


class ToolSelector:
    """Asks GPT-5.2 (OpenAI) which of the candidate tools answers the user's question."""

    @staticmethod
    def select_tool(project, message, tools, previous=None):
        """Returns {"input", "is_find_tool", "tool_array"}, or None if the LLM call failed / is not configured.

        `previous` is the last successful API turns of the chat, oldest first, from
        ConversationRepository.get_recent_api_turns(); it lets the model fill parameters from earlier data.
        """
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
                            "content": f"{_previous_turns_text(previous)}User question: {message}\n\nCandidate tools:\n{json.dumps(tools, default=str)}",
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

        Returns {"input", "output", "is_list", "fieldMapping"}, or None if the call failed / is not
        configured. "fieldMapping" holds only the response values (ids, codes, keys) that a later API
        call could take as an input parameter.
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
        field_mapping = parsed.get("fieldMapping")
        return {
            "input": message,  # always the exact user question
            "output": str(parsed.get("output") or ""),
            "is_list": [str(i) for i in is_list] if isinstance(is_list, list) else [],
            "fieldMapping": field_mapping if isinstance(field_mapping, dict) else {},
        }
