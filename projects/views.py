import json
import logging
import re
from urllib.parse import urljoin

import yaml
import httpx
from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from .models import Project
from api_registry.models import Api
from tools.models import Tool, ToolParameter
from auth_governance.models import AccessRequest
from .credentials import TokenError, fetch_token, mask_secrets, prepare_for_storage
from embeddings.services import index_application, reindex_project
from .serializers import (
    ProjectDetailSerializer,
    OnboardProjectRequestSerializer,
    OnboardProjectResponseSerializer,
    AnalyzeSpecRequestSerializer,
    AnalyzeSpecResponseSerializer,
    GenerateMcpServerRequestSerializer,
    GenerateMcpServerResponseSerializer,
    NavigationCountsSerializer,
    ListMcpServersResponseSerializer,
    McpServerDetailResponseSerializer,
    UpdateMcpServerRequestSerializer,
    CatalogVisibilityRequestSerializer,
    CatalogVisibilityResponseSerializer,
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


def build_auth_headers(auth_config):
    """Authorization header for downloading specs; best-effort, falls back to unauthenticated."""
    try:
        token = fetch_token(auth_config)
    except TokenError as e:
        logger.warning("Token fetch failed (%s) - fetching spec unauthenticated.", e)
        return {}
    return {"Authorization": f"Bearer {token}"} if token else {}


def fetch_spec(url, headers):
    resp = httpx.get(url, headers=headers, timeout=10.0)
    resp.raise_for_status()
    return parse_openapi_spec(resp.text)


def resolve_ref(spec, obj, _depth=0):
    """Follow local "$ref" pointers (e.g. '#/components/parameters/Foo') until a concrete object."""
    while isinstance(obj, dict) and "$ref" in obj and _depth < 10:
        ref = obj["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return {}
        node = spec
        for part in ref[2:].split("/"):
            node = node.get(part.replace("~1", "/").replace("~0", "~")) if isinstance(node, dict) else None
        obj = node
        _depth += 1
    return obj if isinstance(obj, dict) else {}


def collect_parameters(spec, path_item, operation):
    """Path-level + operation-level parameters with $refs resolved; operation overrides path on (name, in)."""
    merged = {}
    for raw in list(path_item.get("parameters") or []) + list(operation.get("parameters") or []):
        param_obj = resolve_ref(spec, raw)
        if not param_obj.get("name"):
            continue
        merged[(param_obj["name"], param_obj.get("in", "query"))] = param_obj
    return list(merged.values())


def compute_base_url(spec, spec_url):
    """Server URL the endpoints are called on.

    OpenAPI 3: first entry of `servers` (a relative URL such as "/v1" is resolved against the
    spec URL; "{var}" placeholders are filled from the variable defaults).
    Swagger 2: `schemes` + `host` + `basePath`.
    """
    servers = spec.get("servers") or []
    if servers and isinstance(servers[0], dict):
        server_url = servers[0].get("url", "")
        for var, meta in (servers[0].get("variables") or {}).items():
            server_url = server_url.replace("{" + var + "}", str((meta or {}).get("default", "")))
        return urljoin(spec_url, server_url) if server_url else ""
    host = spec.get("host")
    if host:
        schemes = spec.get("schemes") or ["https"]
        scheme = "https" if "https" in schemes else schemes[0]
        return f"{scheme}://{host}{spec.get('basePath', '')}".rstrip("/")
    return ""


MAX_SCHEMA_DEPTH = 5


def resolve_schema(spec, schema, depth=0, seen=()):
    """Return a self-contained copy of a JSON schema with every $ref inlined.

    Circular references and anything nested deeper than MAX_SCHEMA_DEPTH are
    replaced by a short stub so the result stays finite and reasonably small.
    """
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or ref in seen or depth >= MAX_SCHEMA_DEPTH:
            name = ref.rsplit("/", 1)[-1] if isinstance(ref, str) else "?"
            return {"type": "object", "description": f"(reference to {name} not expanded)"}
        return resolve_schema(spec, resolve_ref(spec, schema), depth + 1, seen + (ref,))
    if depth >= MAX_SCHEMA_DEPTH:
        return {"type": schema.get("type", "object")}

    out = {}
    for key, value in schema.items():
        if key == "properties" and isinstance(value, dict):
            out[key] = {n: resolve_schema(spec, s, depth + 1, seen) for n, s in value.items()}
        elif key in ("items", "additionalProperties", "not") and isinstance(value, dict):
            out[key] = resolve_schema(spec, value, depth + 1, seen)
        elif key in ("allOf", "oneOf", "anyOf") and isinstance(value, list):
            out[key] = [resolve_schema(spec, s, depth + 1, seen) for s in value]
        else:
            out[key] = value
    return out


def _pick_content(content):
    """Prefer a JSON media type; otherwise take the first one declared."""
    if not isinstance(content, dict) or not content:
        return "", {}
    for ct, media in content.items():
        if "json" in ct:
            return ct, media or {}
    ct, media = next(iter(content.items()))
    return ct, media or {}


def extract_request_body(spec, operation, raw_params):
    """Request body of an operation as {required, contentType, schema}, or {} if it has none.

    Handles OpenAPI 3 `requestBody` and Swagger 2 `in: body` parameters.
    """
    body = resolve_ref(spec, operation.get("requestBody") or {})
    if body:
        content_type, media = _pick_content(body.get("content"))
        return {
            "required": bool(body.get("required", False)),
            "contentType": content_type,
            "schema": resolve_schema(spec, media.get("schema") or {}),
        }
    for param_obj in raw_params:
        if param_obj.get("in") == "body":
            return {
                "required": bool(param_obj.get("required", False)),
                "contentType": "application/json",
                "schema": resolve_schema(spec, param_obj.get("schema") or {}),
            }
    return {}


def extract_success_response(spec, operation):
    """The first 2xx response (else `default`) as {status, description, contentType, schema}, or {}."""
    responses = {str(k): v for k, v in (operation.get("responses") or {}).items()}  # YAML may give int keys
    codes = sorted(c for c in responses if c.startswith("2"))
    code = codes[0] if codes else ("default" if "default" in responses else None)
    if code is None:
        return {}
    resp = resolve_ref(spec, responses[code])
    content_type, media = _pick_content(resp.get("content"))
    schema = media.get("schema") or resp.get("schema") or {}  # `schema` directly on the response = Swagger 2
    return {
        "status": code,
        "description": resp.get("description", ""),
        "contentType": content_type,
        "schema": resolve_schema(spec, schema),
    }


def _schema_type(schema):
    t = schema.get("type", "string") if isinstance(schema, dict) else "string"
    if isinstance(t, list):  # OpenAPI 3.1 allows ["string", "null"]
        t = next((x for x in t if x != "null"), "string")
    return str(t)[:50]


def body_parameters(request_body):
    """Flatten the top-level fields of the request body into parameter dicts (location="body")."""
    schema = (request_body or {}).get("schema") or {}
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        if not schema:
            return []
        # Non-object body (array, primitive, oneOf...) -> one parameter for the whole body.
        return [{
            "name": "body",
            "location": "body",
            "type": _schema_type(schema),
            "required": bool(request_body.get("required", False)),
            "description": schema.get("description", ""),
            "exampleValue": "",
            "enum": [],
        }]
    required = set(schema.get("required") or [])
    result = []
    for name, prop in properties.items():
        prop = prop if isinstance(prop, dict) else {}
        example = prop.get("example", prop.get("default", ""))
        result.append({
            "name": name,
            "location": "body",
            "type": _schema_type(prop),
            "required": name in required,
            "description": prop.get("description", ""),
            "exampleValue": "" if example is None else str(example),
            "enum": prop.get("enum", []),
        })
    return result


def discover_apis(swagger_urls, auth_config=None):
    """Fetch + parse one or more OpenAPI specs into the app.md discovery shape.

    Returns (meta, apis) where meta = {"specVersion", "baseUrl"} taken from
    the first spec, and apis is a flat list across all specs with globally
    unique, stable, sequential string ids ("1", "2", ...).
    """
    headers = build_auth_headers(auth_config)
    meta = {"specVersion": "", "baseUrl": "", "specs": {}}  # specs: per-URL info.title/version/openapi version
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

        info = spec.get("info") or {}
        meta["specs"][url] = {
            "title": info.get("title", ""),
            "description": info.get("description", ""),
            "version": str(info.get("version", "")),
            "openapiVersion": str(spec.get("openapi") or spec.get("swagger") or ""),
            "baseUrl": compute_base_url(spec, url),
        }

        if not meta["specVersion"]:
            openapi_ver = spec.get("openapi")
            swagger_ver = spec.get("swagger")
            if openapi_ver:
                meta["specVersion"] = f"OpenAPI {openapi_ver}"
            elif swagger_ver:
                meta["specVersion"] = f"Swagger {swagger_ver}"
            meta["baseUrl"] = meta["specs"][url]["baseUrl"]

        for path, path_item in (spec.get("paths") or {}).items():
            for method, operation in (path_item or {}).items():
                if method not in HTTP_OPERATION_METHODS:
                    continue

                parameters = []
                raw_params = collect_parameters(spec, path_item, operation)
                request_body = extract_request_body(spec, operation, raw_params)
                for param_obj in raw_params:
                    if param_obj.get("in") == "body":  # Swagger 2 body -> handled as request_body
                        continue
                    schema = resolve_ref(spec, param_obj.get("schema", {}) or {})
                    example = param_obj.get("example", schema.get("example", schema.get("default", "")))
                    parameters.append({
                        "name": param_obj.get("name", ""),
                        "location": param_obj.get("in", "query"),
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
                    # Internal (underscore) keys: persisted by generate_mcp_server, stripped from analyze-spec output.
                    "_swaggerUrl": url,
                    "_requestBody": request_body,
                    "_responseSchema": extract_success_response(spec, operation),
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

    apis = [{k: v for k, v in api.items() if not k.startswith("_")} for api in apis]
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
        meta, discovered_apis = discover_apis(application["swaggerUrls"], application.get("authConfig"))
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
    existing_project = Project.objects.filter(app_code=app_code).first()
    mcp_endpoint_url = f"{settings.MCP_SERVER_BASE_URL.rstrip('/')}/{app_code.lower()}"

    project, _created = Project.objects.update_or_create(
        app_code=app_code,
        defaults={
            "name": application["name"],
            "description": application.get("description", ""),
            "openapi_url": application["swaggerUrls"][0],
            "base_url": meta["baseUrl"],
            "car_id": application.get("carId", ""),
            "owner": application.get("owner", ""),
            "owner_email": application.get("ownerEmail", ""),
            "support_dl": application.get("supportDL", ""),
            "department": application.get("department", ""),
            "swagger_urls": application["swaggerUrls"],
            "auth_config": prepare_for_storage(application.get("authConfig"), existing_project.auth_config if existing_project else None),
            "ai_context": full_ai_context(application.get("aiContext")),
            "status": "active",
            "is_ai_ready": True,
            "mcp_endpoint_url": mcp_endpoint_url,
            "mcp_version": "1.0.0",
            "mcp_transport_type": "Streamable HTTP",
            "mcp_health_status": "Healthy",
        },
    )
    # One Api row per swagger URL, keyed by (project, spec_url). Named after the spec's
    # info.title; the URL is only appended if two specs in this project share a title
    # (name is unique per project).
    api_by_url = {}
    for swagger_url in application["swaggerUrls"]:
        spec_info = meta["specs"].get(swagger_url, {})
        title = (spec_info.get("title") or application["name"])[:300]
        if Api.objects.filter(project=project, name=title).exclude(spec_url=swagger_url).exists():
            title = f"{title} ({swagger_url})"[:300]
        api_by_url[swagger_url], _ = Api.objects.update_or_create(
            project=project,
            spec_url=swagger_url,
            defaults={
                "name": title,
                "base_url": spec_info.get("baseUrl", ""),
                "description": spec_info.get("description") or application.get("description", ""),
                "version": spec_info.get("version", "")[:50],
                "openapi_version": spec_info.get("openapiVersion", "")[:50],
            },
        )

    created_tools = []
    for sel, discovered in resolved:
        tool_name = sel["toolName"]
        tool, _ = Tool.objects.update_or_create(
            project=project,
            name=tool_name,
            defaults={
                "api": api_by_url[discovered["_swaggerUrl"]],
                "display_name": discovered["summary"] or tool_name,
                "description": discovered["description"],
                "http_method": discovered["method"],
                "path": discovered["endpoint"],
                "operation_id": tool_name,
                "summary": discovered["summary"],
                "tags": [discovered["tag"]] if discovered["tag"] else [],
                "request_schema": discovered.get("_requestBody") or {},
                "response_schema": discovered.get("_responseSchema") or {},
                "required_permission": f"MCP_{tool_name.upper()}",
                "status": "active",
            },
        )
        tool.parameters.all().delete()
        all_params = discovered["parameters"] + body_parameters(discovered.get("_requestBody"))
        for param in all_params:
            ToolParameter.objects.update_or_create(
                tool=tool,
                name=param["name"][:200],
                location=param.get("location") or ("path" if f"{{{param['name']}}}" in discovered["endpoint"] else "query"),
                defaults=dict(
                    data_type=param["type"],
                    required=param["required"],
                    description=param["description"],
                    default_value=param["exampleValue"][:500],
                    enum_values=param.get("enum", []),
                ),
            )
        created_tools.append(tool)

    index_application(project, created_tools)

    return Response(
        {
            "application": {
                "id": project.id,
                "publicId": project.public_id,
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


@extend_schema(responses={200: NavigationCountsSerializer})
@api_view(["GET"])
def navigation_counts(request):
    """
    GET /api/navigation/counts
    Badge numbers for the side menu, COUNT queries only.
    """
    return Response(
        {
            "mcpServers": Project.objects.count(),
            "mcpTools": Tool.objects.count(),
            "pendingAccessRequests": AccessRequest.objects.filter(status="pending").count(),
        }
    )


SERVER_STATUSES = {"active": "Active", "maintenance": "Maintenance"}
HEALTH_STATUSES = {"Healthy", "Degraded", "Offline"}


def server_summary(project):
    """One card in the MCP Servers list. Needs tools_count annotated on the project."""
    return {
        "id": f"mcp-{project.id}",
        "name": f"{project.name} MCP",
        "version": project.mcp_version,
        # The UI only knows three states, so pending/failed projects show as Disabled.
        "status": SERVER_STATUSES.get(project.status, "Disabled"),
        "healthStatus": project.mcp_health_status if project.mcp_health_status in HEALTH_STATUSES else "Offline",
        "endpointUrl": project.mcp_endpoint_url,
        "transportType": project.mcp_transport_type,
        "toolsCount": project.tools_count,
        "isPublishedToCatalog": project.is_published_to_catalog,
        "lastDeployed": project.updated_at,
        # No consumer/dependency links are modelled yet.
        "usedByApps": [],
        "dependsOnServers": [],
        "application": {
            "id": str(project.id),
            "publicId": project.public_id,
            "name": project.name,
            "appCode": project.app_code,
            "owner": project.owner,
            "ownerEmail": project.owner_email,
            "department": project.department,
            "isAiReady": project.is_ai_ready,
        },
    }


def full_ai_context(ai_context):
    """All nine aiContext fields, empty where an older record never stored them."""
    empty = {
        "businessPurpose": "",
        "businessDomain": "",
        "keyUseCases": [],
        "commonWorkflows": [],
        "importantTerminology": [],
        "intendedConsumers": [],
        "usageGuidelines": "",
        "restrictions": "",
        "aiGuidance": "",
    }
    return {**empty, **(ai_context or {})}


def application_detail(project):
    # Unlike the server status, the application status also has a Pending state.
    app_status = "Pending" if project.status == "pending" else SERVER_STATUSES.get(project.status, "Disabled")
    auth_config = mask_secrets(project.auth_config)  # secrets are never returned
    return {
        "id": str(project.id),
        "publicId": project.public_id,
        "name": project.name,
        "appCode": project.app_code,
        "carId": project.car_id,
        "description": project.description,
        "owner": project.owner,
        "ownerEmail": project.owner_email,
        "supportDL": project.support_dl,
        "department": project.department,
        "status": app_status,
        "isAiReady": project.is_ai_ready,
        "lastUpdated": project.updated_at,
        "mcpToolsCount": project.tools_count,
        "swaggerUrls": project.swagger_urls,
        "authConfig": auth_config,
        "aiContext": full_ai_context(project.ai_context),
        "aiSummaryConfig": project.ai_summary_config,
    }


def tool_as_api(tool):
    """A saved tool in the shape the detail modal lists its APIs."""
    return {
        "id": str(tool.id),
        "endpoint": tool.path,
        "method": tool.http_method,
        "summary": tool.summary,
        "description": tool.description,
        "tag": tool.tags[0] if tool.tags else "Uncategorized",
        "suggestedToolName": tool.name,
        "enabledForMcp": tool.status == "active",
        "parameters": [
            {
                "name": param.name,
                "location": param.location,
                "type": param.data_type,
                "required": param.required,
                "description": param.description,
                "exampleValue": param.default_value,
            }
            for param in tool.parameters.all()
        ],
    }


NOT_FOUND = {"error": "MCP server not found."}
DETAIL_FIELD_COLUMNS = {
    "name": "name",
    "description": "description",
    "owner": "owner",
    "ownerEmail": "owner_email",
    "supportDL": "support_dl",
    "department": "department",
}
PROJECT_STATUS_BY_LABEL = {"Active": "active", "Maintenance": "maintenance", "Disabled": "disabled"}


def server_detail_payload(project):
    return {
        "server": server_summary(project),
        "application": application_detail(project),
        "apis": [tool_as_api(tool) for tool in project.tools.prefetch_related("parameters")],
    }


@extend_schema(methods=["GET"], responses={200: McpServerDetailResponseSerializer})
@extend_schema(
    methods=["PATCH"],
    request=UpdateMcpServerRequestSerializer,
    responses={200: McpServerDetailResponseSerializer},
)
@api_view(["GET", "PATCH"])
def mcp_server_detail(request, server_id):
    """
    GET   /api/mcp-servers/{id}  full application record plus its saved tools.
    PATCH /api/mcp-servers/{id}  partial update, only the fields sent are changed.
    """
    project = get_project_by_server_id(server_id)
    if project is None:
        return Response(NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(server_detail_payload(project))

    fixed = [key for key in ("appCode", "carId") if key in request.data]
    if fixed:
        return Response(
            {"error": f"{', '.join(fixed)} cannot be changed after registration."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    req = UpdateMcpServerRequestSerializer(data=request.data)
    req.is_valid(raise_exception=True)
    data = req.validated_data

    if "name" in data and Project.objects.filter(name=data["name"]).exclude(id=project.id).exists():
        return Response(
            {"error": f"An application named '{data['name']}' already exists."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    for field, column in DETAIL_FIELD_COLUMNS.items():
        if field in data:
            setattr(project, column, data[field])
    if "status" in data:
        project.status = PROJECT_STATUS_BY_LABEL[data["status"]]
        # Stopping the app takes its MCP server offline; activating brings it back.
        if data["status"] == "Disabled":
            project.mcp_health_status = "Offline"
        elif data["status"] == "Active":
            project.mcp_health_status = "Healthy"
    if "swaggerUrls" in data:
        project.swagger_urls = data["swaggerUrls"]
        project.openapi_url = data["swaggerUrls"][0]
    if "authConfig" in data:
        project.auth_config = prepare_for_storage(data["authConfig"], project.auth_config)
    if "aiContext" in data:
        project.ai_context = data["aiContext"]
    if "aiSummaryConfig" in data:
        project.ai_summary_config = data["aiSummaryConfig"]
    project.save()

    if {"name", "description", "aiContext"} & data.keys():
        reindex_project(project)

    return Response(server_detail_payload(project))


@extend_schema(responses={200: ListMcpServersResponseSerializer})
@api_view(["GET"])
def list_mcp_servers(request):
    """
    GET /api/mcp-servers
    Every registered MCP server with its application info and tool count.
    """
    projects = Project.objects.annotate(tools_count=Count("tools"))
    return Response({"servers": [server_summary(p) for p in projects]})


def get_project_by_server_id(server_id):
    """Project (with tools_count) for an id like 'mcp-1', or None."""
    match = re.fullmatch(r"mcp-(\d+)", server_id)
    if not match:
        return None
    return Project.objects.annotate(tools_count=Count("tools")).filter(id=int(match.group(1))).first()


@extend_schema(
    request=CatalogVisibilityRequestSerializer,
    responses={200: CatalogVisibilityResponseSerializer},
)
@api_view(["PUT"])
def set_catalog_visibility(request, server_id):
    """
    PUT /api/mcp-servers/{id}/catalog-visibility
    Publish a server to the MCP Catalog or make it private. Idempotent.
    """
    project = get_project_by_server_id(server_id)
    if project is None:
        return Response({"error": "MCP server not found."}, status=status.HTTP_404_NOT_FOUND)

    # Strict bool check: DRF's BooleanField would also accept "true", 1, etc.
    value = request.data.get("isPublishedToCatalog") if hasattr(request.data, "get") else None
    if not isinstance(value, bool):
        return Response(
            {"error": "isPublishedToCatalog is required and must be a boolean."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    project.is_published_to_catalog = value
    project.save(update_fields=["is_published_to_catalog"])
    return Response({"id": f"mcp-{project.id}", "isPublishedToCatalog": value})
