import json
import re
import time
from urllib.parse import quote

import httpx
from django.conf import settings
from django.db.models import Count
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import OpenApiParameter, extend_schema
from projects.credentials import TokenError, fetch_token
from embeddings.services import reindex_tool, remove_tool_from_index
from projects.models import Project
from .models import Tool
from .serializers import (
    ExecuteToolRequestSerializer,
    ExecuteToolResponseSerializer,
    ListDiscoveredEndpointsResponseSerializer,
    ListMcpToolsResponseSerializer,
    ToolDetailResponseSerializer,
    ToolDetailSerializer,
    UpdateToolRequestSerializer,
)


@extend_schema(responses={200: ToolDetailSerializer(many=True)})
@api_view(["GET"])
def project_tools(request, project_id):
    """
    GET /api/projects/{id}/tools
    Fetch a project and all its tools with nested parameters.
    """
    try:
        project = Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return Response(
            {"error": f"Project with id {project_id} not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    tools = Tool.objects.filter(project=project).prefetch_related("parameters")
    tools_serializer = ToolDetailSerializer(tools, many=True)

    return Response(
        {
            "project": project.name,
            "tools": tools_serializer.data,
        },
        status=status.HTTP_200_OK,
    )


def tool_status_label(tool):
    """Tools have no 'Needs Configuration' column: an enabled tool with no permission set is treated as one."""
    if tool.status != "active":
        return "Disabled"
    return "Active" if tool.required_permission else "Needs Configuration"


def ai_readiness_score(tool):
    """0-100, calculated on read: min(100, 75 + 5 x sample inputs + 3 x sample outputs)."""
    return min(100, 75 + 5 * len(tool.sample_inputs) + 3 * len(tool.sample_outputs))


def is_ai_ready(tool):
    return bool(tool.description.strip() and tool.when_to_use.strip() and tool.when_not_to_use.strip())


def tool_card(tool):
    project = tool.project
    return {
        "id": f"tool-{tool.id}",
        "name": tool.name,
        "displayName": tool.display_name or tool.name,
        "description": tool.description,
        "sourceEndpoint": tool.path,
        "httpMethod": tool.http_method,
        "serverId": f"mcp-{project.id}",
        "serverName": f"{project.name} MCP",
        "applicationId": str(project.id),
        "applicationName": project.name,
        "requiredPermission": tool.required_permission,
        "status": tool_status_label(tool),
        "isAiReady": is_ai_ready(tool),
        "aiReadinessScore": ai_readiness_score(tool),
        "sampleInputsCount": len(tool.sample_inputs),
        "sampleOutputsCount": len(tool.sample_outputs),
        # No usage data is stored yet.
        "lastUsed": None,
        "callCount": 0,
    }


@extend_schema(
    parameters=[OpenApiParameter("serverId", str, required=False, description="Only this server's tools, e.g. mcp-1")],
    responses={200: ListMcpToolsResponseSerializer},
)
@api_view(["GET"])
def list_mcp_tools(request):
    """
    GET /api/mcp-tools[?serverId=mcp-1]
    Every MCP tool with its server / application info.
    """
    # Disabled rows are discovered-but-not-enabled endpoints; they only show on API Discovery.
    tools = Tool.objects.select_related("project").filter(status="active")
    server_id = request.query_params.get("serverId")
    if server_id:
        match = re.fullmatch(r"mcp-(\d+)", server_id)
        # An unknown or malformed server id simply has no tools.
        tools = tools.filter(project_id=int(match.group(1))) if match else tools.none()
    return Response({"tools": [tool_card(t) for t in tools]})


@extend_schema(responses={200: ListDiscoveredEndpointsResponseSerializer})
@api_view(["GET"])
def list_discovered_endpoints(request):
    """
    GET /api/api-discovery
    Every stored endpoint, enabled or not, for the API Discovery page.
    """
    tools = (
        Tool.objects.select_related("project")
        .annotate(parameters_count=Count("parameters"))
        .order_by("project__name", "path", "http_method")
    )
    return Response(
        {
            "endpoints": [
                {
                    "id": f"tool-{tool.id}",
                    "endpoint": tool.path,
                    "method": tool.http_method,
                    "summary": tool.summary,
                    "description": tool.description,
                    "tag": tool.tags[0] if tool.tags else "",
                    "suggestedToolName": tool.name,
                    "enabledForMcp": tool.status == "active",
                    "parametersCount": tool.parameters_count,
                    "applicationId": str(tool.project_id),
                    "applicationName": tool.project.name,
                    "serverId": f"mcp-{tool.project_id}",
                    "serverName": f"{tool.project.name} MCP",
                }
                for tool in tools
            ]
        }
    )


FIXED_TOOL_FIELDS =["name", "displayName", "sourceEndpoint", "httpMethod", "serverId"]


def get_tool_by_id(tool_id):
    """Tool (with project and parameters) for an id like 'tool-1', or None."""
    match = re.fullmatch(r"tool-(\d+)", tool_id)
    if not match:
        return None
    return Tool.objects.select_related("project").prefetch_related("parameters").filter(id=int(match.group(1))).first()


def tool_detail_payload(tool):
    card = tool_card(tool)
    for key in ("sampleInputsCount", "sampleOutputsCount", "lastUsed", "callCount"):
        del card[key]
    response_schema = tool.response_schema if isinstance(tool.response_schema, dict) else {}
    return {
        **card,
        "whenToUse": tool.when_to_use,
        "whenNotToUse": tool.when_not_to_use,
        "callSequence": tool.call_sequence,
        "outputSchemaDescription": response_schema.get("description", ""),
        "inputs": [
            {
                "name": p.name,
                "location": p.location,
                "type": p.data_type,
                "required": p.required,
                "description": p.description,
                "exampleValue": p.default_value,
            }
            for p in tool.parameters.all()
        ],
        "sampleInputs": tool.sample_inputs,
        "sampleOutputs": tool.sample_outputs,
    }


def with_sample_ids(samples, prefix, kind):
    """Keeps the ids the client sent; new samples get the next free '<prefix>-N'."""
    used = [int(m.group(1)) for s in samples if (m := re.fullmatch(prefix + r"-(\d+)", str(s.get("id") or "")))]
    next_number = max(used, default=0) + 1
    result = []
    for sample in samples:
        sample_id = sample.get("id") or ""
        if not sample_id:
            sample_id, next_number = f"{prefix}-{next_number}", next_number + 1
        result.append({
            "id": str(sample_id),
            "name": sample["name"].strip(),
            "description": sample.get("description") or "",
            "type": "success" if kind == "input" else (sample.get("type") or "success"),
            "payload": sample["payload"],
        })
    return result


@extend_schema(methods=["GET"], responses={200: ToolDetailResponseSerializer})
@extend_schema(methods=["PATCH"], request=UpdateToolRequestSerializer, responses={200: ToolDetailResponseSerializer})
@api_view(["GET", "PATCH"])
def mcp_tool_detail(request, tool_id):
    """
    GET   /api/mcp-tools/{id}  everything the Configure Tool popup shows.
    PATCH /api/mcp-tools/{id}  partial update of the fields that popup edits.
    """
    tool = get_tool_by_id(tool_id)
    if tool is None:
        return Response({"error": "MCP tool not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response({"tool": tool_detail_payload(tool)})

    fixed = [key for key in FIXED_TOOL_FIELDS if key in request.data]
    if fixed:
        return Response({"error": f"{', '.join(fixed)} cannot be changed here."}, status=status.HTTP_400_BAD_REQUEST)

    req = UpdateToolRequestSerializer(data=request.data)
    req.is_valid(raise_exception=True)
    data = req.validated_data

    columns = {
        "description": "description",
        "requiredPermission": "required_permission",
        "whenToUse": "when_to_use",
        "whenNotToUse": "when_not_to_use",
        "callSequence": "call_sequence",
    }
    for field, column in columns.items():
        if field in data:
            setattr(tool, column, data[field])
    if "sampleInputs" in data:
        tool.sample_inputs = with_sample_ids(data["sampleInputs"], "si", "input")
    if "sampleOutputs" in data:
        tool.sample_outputs = with_sample_ids(data["sampleOutputs"], "so", "output")

    was_active = tool.status == "active"
    if "status" in data:
        tool.status = data["status"].lower()
    tool.save()

    # Vector DB (best effort): disabled tools are not searchable, enabled ones need a current vector.
    if tool.status != "active":
        if was_active:
            remove_tool_from_index(tool.id)
    elif not was_active or {"description", "whenToUse", "whenNotToUse"} & data.keys():
        reindex_tool(tool)

    return Response({"tool": tool_detail_payload(tool)})


UPSTREAM_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 1024 * 1024
PATH_PLACEHOLDER = re.compile(r"\{([^{}]+)\}")


def _blank(value):
    return value is None or value == ""


def build_tool_request(tool, values):
    """Place each input value by its saved parameter location.

    Returns (url, query, headers, cookies, body). Raises ValueError("Missing required parameter: x").
    """
    project = tool.project
    base_url = (tool.api.base_url or project.base_url).rstrip("/")
    if not base_url:
        raise ValueError("The application has no base URL configured.")

    query, headers, cookies, body = {}, {}, {}, {}
    for param in tool.parameters.all():
        value = values.get(param.name)
        if _blank(value):
            if param.required:
                raise ValueError(f"Missing required parameter: {param.name}")
            continue
        if param.location == "query":
            query[param.name] = value
        elif param.location == "header":
            headers[param.name] = str(value)
        elif param.location == "cookie":
            cookies[param.name] = str(value)
        elif param.location == "body":
            body[param.name] = value

    def fill(match):
        value = values.get(match.group(1))
        if _blank(value):
            raise ValueError(f"Missing required parameter: {match.group(1)}")
        return quote(str(value), safe="")

    path = PATH_PLACEHOLDER.sub(fill, tool.path)
    return base_url + path, query, headers, cookies, body


def read_limited(response):
    """Body text, capped at MAX_RESPONSE_BYTES. Returns (text, truncated)."""
    chunks, size = [], 0
    for chunk in response.iter_bytes():
        size += len(chunk)
        chunks.append(chunk)
        if size > MAX_RESPONSE_BYTES:
            break
    data = b"".join(chunks)
    truncated = len(data) > MAX_RESPONSE_BYTES
    return data[:MAX_RESPONSE_BYTES].decode(response.encoding or "utf-8", errors="replace"), truncated


def execute_result(**overrides):
    result = {"success": False, "httpStatus": None, "durationMs": 0, "request": None, "response": None, "error": None}
    result.update(overrides)
    return result


@extend_schema(request=ExecuteToolRequestSerializer, responses={200: ExecuteToolResponseSerializer})
@api_view(["POST"])
def execute_mcp_tool(request, tool_id):
    """
    POST /api/mcp-tools/{id}/execute
    Calls the tool's real backend API once with the given input and returns what came back.
    Nothing is saved. Upstream failures are still HTTP 200 with success=false.
    """
    match = re.fullmatch(r"tool-(\d+)", tool_id)
    tool = (
        Tool.objects.select_related("project", "api").prefetch_related("parameters").filter(id=int(match.group(1))).first()
        if match
        else None
    )
    if tool is None:
        return Response({"error": "MCP tool not found."}, status=status.HTTP_404_NOT_FOUND)

    values = request.data.get("input") if hasattr(request.data, "get") else None
    if not isinstance(values, dict):
        return Response({"error": "'input' must be a JSON object."}, status=status.HTTP_400_BAD_REQUEST)

    if tool_status_label(tool) != "Active" or tool.project.status != "active":
        return Response({"error": "Tool is not active."}, status=status.HTTP_409_CONFLICT)

    try:
        url, query, headers, cookies, body = build_tool_request(tool, values)
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    # Spotify override: use the bearer token from .env instead of calling the token API.
    if "spotify" in tool.project.name.lower() and settings.SPOTIFY_BEARER_TOKEN:
        token = settings.SPOTIFY_BEARER_TOKEN
    else:
        try:
            token = fetch_token(tool.project.auth_config)
        except TokenError as e:
            return Response(execute_result(error=f"Could not get an access token: {e}"))
    if token:
        headers = {"Authorization": f"Bearer {token}", **headers}

    method = tool.http_method
    with httpx.Client(timeout=UPSTREAM_TIMEOUT, follow_redirects=False) as client:
        upstream_request = client.build_request(
            method,
            url,
            params=query or None,
            headers=headers,
            cookies=cookies or None,
            json=body if body else None,
        )
        shown = {"method": method, "url": str(upstream_request.url)}
        started = time.monotonic()
        try:
            upstream = client.send(upstream_request, stream=True)
            try:
                text, truncated = read_limited(upstream)
                http_status = upstream.status_code
            finally:
                upstream.close()
        except httpx.TimeoutException:
            return Response(execute_result(request=shown, error=f"Timed out after {int(UPSTREAM_TIMEOUT)}s"))
        except httpx.HTTPError:
            return Response(execute_result(request=shown, error="Could not reach the upstream API"))
        duration_ms = int((time.monotonic() - started) * 1000)

    parsed = text
    if text and not truncated:
        try:
            parsed = json.loads(text)
        except ValueError:
            pass

    ok = 200 <= http_status < 300
    error = None
    if not ok:
        error = f"Upstream returned {http_status}"
    elif truncated:
        error = "Response was larger than 1 MB and was truncated"
    return Response(
        execute_result(
            success=ok,
            httpStatus=http_status,
            durationMs=duration_ms,
            request=shown,
            response=parsed if text else None,
            error=error,
        )
    )
