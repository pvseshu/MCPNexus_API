import json
import yaml
import httpx
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from .models import Project
from api_registry.models import Api
from tools.models import Tool, ToolParameter
from .serializers import (
    ProjectDetailSerializer,
    OnboardProjectRequestSerializer,
    OnboardProjectResponseSerializer,
)


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
