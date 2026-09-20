"""OpenAI (GPT-5.2) calls for the chat pipeline, over plain HTTP like scripts/ask_gpt.py."""
import json
import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

TOOL_SELECT_TIMEOUT_SECONDS = 90

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
