from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema
from projects.models import Project
from .models import Tool
from .serializers import ToolDetailSerializer


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
