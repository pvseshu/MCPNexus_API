"""Database access for the chat pipeline."""
import logging

from tools.models import Tool

from .models import ConversationLog

logger = logging.getLogger(__name__)


class ToolRepository:
    @staticmethod
    def get_matched_tool_details(project, matches):
        """Full Postgres details (with parameters) of the tools Qdrant matched, by tool name, best match first.

        Only active tools of `project` are returned; a name found in Qdrant but missing or disabled in
        Postgres (stale vector) is skipped.
        """
        by_name = {
            t.name: t
            for t in Tool.objects.filter(project=project, name__in=[m["name"] for m in matches], status="active")
            .select_related("api")
            .prefetch_related("parameters")
        }
        details = []
        for m in matches:
            tool = by_name.get(m["name"])
            if tool is None:
                logger.warning("[chat] tool %r matched in Qdrant but is missing/disabled in Postgres.", m["name"])
                continue
            details.append(
                {
                    "score": m["score"],
                    "id": tool.id,
                    "name": tool.name,
                    "display_name": tool.display_name,
                    "description": tool.description,
                    "summary": tool.summary,
                    "http_method": tool.http_method,
                    "path": tool.path,
                    "operation_id": tool.operation_id,
                    "tags": tool.tags,
                    "required_permission": tool.required_permission,
                    "required_security_groups": tool.required_security_groups,
                    "when_to_use": tool.when_to_use,
                    "when_not_to_use": tool.when_not_to_use,
                    "call_sequence": tool.call_sequence,
                    "request_schema": tool.request_schema,
                    "sample_inputs": tool.sample_inputs,
                    "parameters": [
                        {
                            "name": p.name,
                            "location": p.location,
                            "data_type": p.data_type,
                            "required": p.required,
                            "description": p.description,
                            "default_value": p.default_value,
                            "enum_values": p.enum_values,
                        }
                        for p in tool.parameters.all()
                    ],
                }
            )
        return details

    @staticmethod
    def get_tool_for_call(project, name):
        """The tool row (with its project, api and parameters) needed to call its backend API, or None."""
        return (
            Tool.objects.select_related("project", "api")
            .prefetch_related("parameters")
            .filter(project=project, name=name)
            .first()
        )


class ConversationRepository:
    @staticmethod
    def save_turn(user, session_id, project, message, reply, trace):
        """Save one chat turn: the question, the reply, and what happened on the way (tools, API calls).

        `reply` is the {"status", "message", "list"} dict returned to the client; `trace` is filled in by the pipeline.
        """
        return ConversationLog.objects.create(
            user=user,
            session_id=session_id,
            matched_project=project,
            project_name=project.name,
            question=message,
            final_answer=reply["message"],
            answer_items=reply["list"],
            status=reply["status"],
            outcome=trace["outcome"],
            api_called=bool(trace["result_summary"]),
            tools_matched=trace["tools_matched"],
            tools_called=trace["tools_called"],
            parameters_used=trace["parameters_used"],
            result_summary=trace["result_summary"],
            llm_formated_resp=trace.get("field_mapping") or None,
        )

    @staticmethod
    def get_recent_api_turns(session_id, project, limit=3):
        """The last `limit` turns of this chat window where an API was called and the turn succeeded, oldest first.

        Each item: {"question", "answer", "tools_called", "api_responses"}. `api_responses` is
        {tool_name: response JSON text}; the text is the (possibly cut short) copy saved in the log.
        """
        rows = list(
            ConversationLog.objects.filter(
                session_id=session_id, matched_project=project, api_called=True, status="success"
            ).order_by("-id")[:limit]
        )
        return [
            {
                "question": r.question,
                "answer": r.final_answer,
                "tools_called": r.tools_called,
                "api_responses": r.llm_formated_resp or {},
            }
            for r in reversed(rows)
        ]
