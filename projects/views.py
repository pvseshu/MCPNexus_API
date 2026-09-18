import json
import logging
import re

import yaml
import httpx
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from .models import Project
from api_registry.models import Api
from tools.models import Tool, ToolParameter
from embeddings.services import index_application
from .serializers import (
    ProjectDetailSerializer,
    OnboardProjectRequestSerializer,
    OnboardProjectResponseSerializer,
    AnalyzeSpecRequestSerializer,
    AnalyzeSpecResponseSerializer,
    GenerateMcpServerRequestSerializer,
    GenerateMcpServerResponseSerializer,
)

logger = logging.getLogger(__name__)


def generate_tool_name(method, path):
    """Generate a tool name from HTTP method and path if operationId is missing."""
    # Clean path: /users/{id}/posts -> users_id_posts
    clean_path = path.strip("/").replace("{", "").replace("}", "_").replace("-", "_").replace("/", "_")
    return f"{method.lower()}_{clean_path}".lower()


def parse_openapi_spec(spec_content):
    """
    Parse OpenAPI spec (JSON or YAML).
    Returns dict with parsed spec or raises ValueError.
    """
    try:
        # Try JSON first
        return json.loads(spec_content)
    except json.JSONDecodeError:
        try:
            # Fall back to YAML
            return yaml.safe_load(spec_content)
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse spec as JSON or YAML: {str(e)}")


def extract_parameter_info(param_obj):
    """Extract parameter info from OpenAPI parameter object."""
    schema = param_obj.get("schema", {})
    if isinstance(schema, dict):
        data_type = schema.get("type", "string")
        enum_values = schema.get("enum", [])
    else:
        data_type = "string"
        enum_values = []

    return {
        "name": param_obj.get("name", ""),
        "location": param_obj.get("in", "query"),  # query, path, header, cookie
        "data_type": data_type,
        "required": param_obj.get("required", False),
        "description": param_obj.get("description", ""),
        "enum_values": enum_values,
    }


@extend_schema(
    request=OnboardProjectRequestSerializer,
    responses={201: OnboardProjectResponseSerializer},
)
@api_view(["POST"])
@transaction.atomic
def onboard_project(request):
    """
    POST /api/projects/onboard
    Download OpenAPI spec, parse it, and create Project + Api + Tools + Parameters.
    """
    try:
        data = request.data
        name = data.get("name", "").strip()
        openapi_url = data.get("openapi_url", "").strip()

        if not name:
            return Response(
                {"error": "Missing required field: name"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not openapi_url:
            return Response(
                {"error": "Missing required field: openapi_url"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Download OpenAPI spec
        try:
            response = httpx.get(openapi_url, timeout=10.0)
            response.raise_for_status()
            spec_content = response.text
        except httpx.TimeoutException:
            return Response(
                {"error": f"Timeout downloading OpenAPI spec from {openapi_url}"},
                status=status.HTTP_408_REQUEST_TIMEOUT,
            )
        except httpx.HTTPStatusError as e:
            return Response(
                {"error": f"Failed to download OpenAPI spec: HTTP {e.response.status_code}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                {"error": f"Failed to download OpenAPI spec: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse OpenAPI spec
        try:
            spec = parse_openapi_spec(spec_content)
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Extract metadata
        info = spec.get("info", {})
        servers = spec.get("servers", [])
        base_url = servers[0].get("url", "") if servers else ""
        openapi_version = spec.get("openapi", "")
        paths = spec.get("paths", {})

        # Create Project
        project = Project.objects.create(
            name=name,
            description=info.get("description", ""),
            openapi_url=openapi_url,
            base_url=base_url,
            status="active",
        )

        # Create Api record
        api = Api.objects.create(
            project=project,
            name=info.get("title", name),
            version=info.get("version", ""),
            openapi_version=openapi_version,
            base_url=base_url,
            description=info.get("description", ""),
        )

        # Process paths and create Tools + Parameters
        tools_created = 0
        for path, path_item in paths.items():
            for method, operation in path_item.items():
                # Skip non-operation keys (like "parameters")
                if method not in ["get", "post", "put", "patch", "delete", "head", "options"]:
                    continue

                operation_id = operation.get("operationId")
                if not operation_id:
                    operation_id = generate_tool_name(method.upper(), path)

                # Create Tool
                tool = Tool.objects.create(
                    project=project,
                    api=api,
                    name=operation_id,
                    http_method=method.upper(),
                    path=path,
                    description=operation.get("description", operation.get("summary", "")),
                    operation_id=operation_id,
                    summary=operation.get("summary", ""),
                    tags=operation.get("tags", []),
                    request_schema=operation.get("requestBody", {}),
                    response_schema=operation.get("responses", {}),
                )
                tools_created += 1

                # Process parameters
                parameters = operation.get("parameters", [])
                for param_obj in parameters:
                    param_info = extract_parameter_info(param_obj)
                    ToolParameter.objects.create(
                        tool=tool,
                        name=param_info["name"],
                        location=param_info["location"],
                        data_type=param_info["data_type"],
                        required=param_info["required"],
                        description=param_info["description"],
                        enum_values=param_info["enum_values"],
                    )

        return Response(
            {
                "project_id": project.id,
                "project_name": project.name,
                "apis_found": 1,
                "tools_created": tools_created,
            },
            status=status.HTTP_201_CREATED,
        )

    except Exception as e:
        # Transaction will roll back automatically due to @transaction.atomic
        return Response(
            {"error": f"Unexpected error: {str(e)}"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# --- App Registration wizard (API.md) ------------------------------------
#
# 1. POST /api/app-registration/analyze-spec  - read-only, no persistence.
# 2. POST /api/app-registration/generate      - the only step that writes
#    Project/Api/Tool rows and (best-effort) indexes into Qdrant.

HTTP_OPERATION_METHODS = ["get", "post", "put", "patch", "delete", "head", "options"]
VERB_PREFIX = {"get": "get", "post": "create", "put": "update", "patch": "update", "delete": "delete"}


def generate_camel_tool_name(method, path):
    """e.g. GET /customers/{customerId}/transactions -> getCustomerTransactions"""
    words = []
    for segment in re.split(r"[/{}]", path):
        for word in re.split(r"[-_]", segment):
            if word and not word.isdigit():
                words.append(word)
    if not words:
        words = ["resource"]
    camel = words[0].lower() + "".join(w[:1].upper() + w[1:] for w in words[1:])
    verb = VERB_PREFIX.get(method.lower(), method.lower())
    return verb + camel[:1].upper() + camel[1:]


def get_authblue_token(auth_blue_cfg):
    """Best-effort fetch of an AuthBlue bearer token to authenticate spec fetches.

    AuthBlue is an internal service - if it isn't reachable from this
    environment we log a warning and fall back to an unauthenticated fetch
    rather than failing the whole request outright.
    """
    token_url = auth_blue_cfg.get("tokenUrl")
    if not token_url:
        return None
    try:
        resp = httpx.post(
            token_url,
            json={
                "serviceId": auth_blue_cfg.get("serviceId"),
                "servicePassword": auth_blue_cfg.get("servicePassword"),
                "scopeGroups": auth_blue_cfg.get("scopeGroups", []),
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("access_token") or data.get("token")
    except Exception as e:
        logger.warning("AuthBlue token fetch from %s failed (%s) - fetching spec unauthenticated.", token_url, e)
        return None


def build_auth_headers(auth_config):
    if not auth_config:
        return {}
    if auth_config.get("type") == "authblue" and auth_config.get("authBlue"):
        token = get_authblue_token(auth_config["authBlue"])
        if token:
            return {"Authorization": f"Bearer {token}"}
    return {}


def _sanitize_auth_config(auth_config):
    """Strip secrets (e.g. servicePassword) before persisting auth_config."""
    if not auth_config:
        return {}
    sanitized = json.loads(json.dumps(auth_config))
    auth_blue = sanitized.get("authBlue")
    if isinstance(auth_blue, dict) and "servicePassword" in auth_blue:
        auth_blue["servicePassword"] = ""
    return sanitized


def fetch_spec(url, headers):
    resp = httpx.get(url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    return parse_openapi_spec(resp.text)


def discover_apis(swagger_urls, auth_config=None):
    """Fetch + parse one or more OpenAPI specs into the app.md discovery shape.

    Returns (meta, apis) where meta = {"specVersion", "baseUrl"} taken from
    the first spec, and apis is a flat list across all specs with globally
    unique, stable, sequential string ids ("1", "2", ...).
    """
    headers = build_auth_headers(auth_config)
    meta = {"specVersion": "", "baseUrl": ""}
    apis = []
    next_id = 1

    for url in swagger_urls:
        try:
            spec = fetch_spec(url, headers)
        except httpx.TimeoutException:
            raise ValueError(f"Timeout downloading OpenAPI spec from {url}")
        except httpx.HTTPStatusError as e:
            raise ValueError(f"Failed to download OpenAPI spec from {url}: HTTP {e.response.status_code}")
        except Exception as e:
            raise ValueError(f"Failed to download or parse OpenAPI spec from {url}: {e}")

        if not meta["specVersion"]:
            openapi_ver = spec.get("openapi")
            swagger_ver = spec.get("swagger")
            if openapi_ver:
                meta["specVersion"] = f"OpenAPI {openapi_ver}"
            elif swagger_ver:
                meta["specVersion"] = f"Swagger {swagger_ver}"
            servers = spec.get("servers", [])
            meta["baseUrl"] = servers[0].get("url", "") if servers else ""

        for path, path_item in (spec.get("paths") or {}).items():
            for method, operation in (path_item or {}).items():
                if method not in HTTP_OPERATION_METHODS:
                    continue

                parameters = []
                for param_obj in operation.get("parameters", []):
                    schema = param_obj.get("schema", {}) or {}
                    example = param_obj.get("example", schema.get("example", schema.get("default", "")))
                    parameters.append({
                        "name": param_obj.get("name", ""),
                        "type": schema.get("type", "string"),
                        "required": bool(param_obj.get("required", False)),
                        "description": param_obj.get("description", ""),
                        "exampleValue": "" if example is None else str(example),
                    })

                tags = operation.get("tags") or ["Uncategorized"]
                apis.append({
                    "id": str(next_id),
                    "endpoint": path,
                    "method": method.upper(),
                    "summary": operation.get("summary", ""),
                    "description": operation.get("description", operation.get("summary", "")),
                    "tag": tags[0],
                    "suggestedToolName": operation.get("operationId") or generate_camel_tool_name(method, path),
                    "parameters": parameters,
                })
                next_id += 1

    return meta, apis


@extend_schema(
    request=AnalyzeSpecRequestSerializer,
    responses={200: AnalyzeSpecResponseSerializer},
)
@api_view(["POST"])
def analyze_spec(request):
    """
    POST /api/app-registration/analyze-spec
    Fetch + parse the given OpenAPI spec(s) and return discovered endpoints.
    Read-only: nothing is persisted here.
    """
    req = AnalyzeSpecRequestSerializer(data=request.data)
    req.is_valid(raise_exception=True)
    data = req.validated_data

    try:
        meta, apis = discover_apis(data["swaggerUrls"], data.get("authConfig"))
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    tag_groups = sorted({api["tag"] for api in apis})

    return Response(
        {
            "specVersion": meta["specVersion"],
            "baseUrl": meta["baseUrl"],
            "totalApisDiscovered": len(apis),
            "tagGroups": tag_groups,
            "apis": apis,
        },
        status=status.HTTP_200_OK,
    )


@extend_schema(
    request=GenerateMcpServerRequestSerializer,
    responses={201: GenerateMcpServerResponseSerializer},
)
@api_view(["POST"])
@transaction.atomic
def generate_mcp_server(request):
    """
    POST /api/app-registration/generate
    The only step that persists data: saves the application, its AI context,
    and the selected APIs as MCP tools, then (best-effort) indexes the AI
    context / tool descriptions into the vector DB.
    """
    req = GenerateMcpServerRequestSerializer(data=request.data)
    req.is_valid(raise_exception=True)
    application = req.validated_data["application"]
    selected_apis = req.validated_data["selectedApis"]

    # Re-run discovery against the same specs to recover full parameter
    # details for the selected APIs (the client only echoes back id/endpoint/
    # method/toolName, not the full discovered payload).
    try:
        _, discovered_apis = discover_apis(application["swaggerUrls"], application.get("authConfig"))
    except ValueError as e:
        return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    by_id = {api["id"]: api for api in discovered_apis}
    by_endpoint_method = {(api["endpoint"], api["method"]): api for api in discovered_apis}

    resolved = []
    for sel in selected_apis:
        discovered = by_id.get(sel["id"]) or by_endpoint_method.get((sel["endpoint"], sel["method"].upper()))
        if not discovered:
            return Response(
                {"error": f"Selected API {sel['method']} {sel['endpoint']} was not found when re-analyzing the spec."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resolved.append((sel, discovered))

    app_code = application["appCode"]
    mcp_endpoint_url = f"{settings.MCP_SERVER_BASE_URL.rstrip('/')}/{app_code.lower()}"

    project, _created = Project.objects.update_or_create(
        app_code=app_code,
        defaults={
            "name": application["name"],
            "description": application.get("description", ""),
            "openapi_url": application["swaggerUrls"][0],
            "car_id": application.get("carId", ""),
            "owner": application.get("owner", ""),
            "owner_email": application.get("ownerEmail", ""),
            "support_dl": application.get("supportDL", ""),
            "department": application.get("department", ""),
            "swagger_urls": application["swaggerUrls"],
            "auth_config": _sanitize_auth_config(application.get("authConfig")),
            "ai_context": application.get("aiContext", {}),
            "status": "active",
            "is_ai_ready": True,
            "mcp_endpoint_url": mcp_endpoint_url,
            "mcp_version": "1.0.0",
            "mcp_transport_type": "Streamable HTTP",
            "mcp_health_status": "Healthy",
        },
    )
    for swagger_url in application["swaggerUrls"]:
        Api.objects.get_or_create(
            project=project,
            name=f"{application['name']} ({swagger_url})"[:300],
            defaults={"description": application.get("description", ""), "base_url": swagger_url},
        )

    created_tools = []
    for sel, discovered in resolved:
        tool_name = sel["toolName"]
        tool, _ = Tool.objects.update_or_create(
            project=project,
            name=tool_name,
            defaults={
                "api": project.apis.first(),
                "display_name": discovered["summary"] or tool_name,
                "description": discovered["description"],
                "http_method": discovered["method"],
                "path": discovered["endpoint"],
                "operation_id": tool_name,
                "summary": discovered["summary"],
                "tags": [discovered["tag"]] if discovered["tag"] else [],
                "required_permission": f"MCP_{tool_name.upper()}",
                "status": "active",
            },
        )
        tool.parameters.all().delete()
        for param in discovered["parameters"]:
            ToolParameter.objects.create(
                tool=tool,
                name=param["name"],
                location="path" if f"{{{param['name']}}}" in discovered["endpoint"] else "query",
                data_type=param["type"],
                required=param["required"],
                description=param["description"],
                default_value=param["exampleValue"],
            )
        created_tools.append(tool)

    index_application(project, created_tools)

    return Response(
        {
            "application": {
                "id": project.id,
                "name": project.name,
                "appCode": project.app_code,
                "carId": project.car_id,
                "status": project.status.capitalize(),
                "isAiReady": project.is_ai_ready,
                "lastUpdated": project.updated_at,
            },
            "mcpServer": {
                "id": f"mcp-{project.id}",
                "name": f"{project.name} MCP",
                "version": project.mcp_version,
                "endpointUrl": project.mcp_endpoint_url,
                "transportType": project.mcp_transport_type,
                "healthStatus": project.mcp_health_status,
            },
            "mcpTools": [
                {
                    "id": f"tool-{tool.id}",
                    "name": tool.name,
                    "displayName": tool.display_name,
                    "sourceEndpoint": tool.path,
                    "httpMethod": tool.http_method,
                    "requiredPermission": tool.required_permission,
                    "status": tool.status.capitalize(),
                }
                for tool in created_tools
            ],
        },
        status=status.HTTP_201_CREATED,
    )
