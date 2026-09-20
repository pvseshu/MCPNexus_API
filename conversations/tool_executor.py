"""Calls the real backend APIs of the tools the LLM selected."""
from tools.views import call_tool, execute_result, tool_status_label

from .repository import ToolRepository


class ToolExecutor:
    @staticmethod
    def call(project, item):
        """Call the API of one tool chosen by the LLM ({"tool_name", "parameters"}); returns the execute_result dict."""
        tool = ToolRepository.get_tool_for_call(project, item["tool_name"])
        if tool is None:
            return execute_result(error=f"Tool {item['tool_name']!r} not found.")
        if tool_status_label(tool) != "Active" or project.status != "active":
            return execute_result(error=f"Tool {tool.name!r} is not active.")
        try:
            return call_tool(tool, item["parameters"])
        except ValueError as e:  # parameters do not fit the tool (e.g. a required one is missing)
            return execute_result(error=str(e))
