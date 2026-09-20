"""Database access for the chat pipeline (Postgres reads only)."""
import logging

from tools.models import Tool

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
