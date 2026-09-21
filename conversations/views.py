from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from projects.models import Project
from .serializers import SendChatRequestSerializer, SendChatResponseSerializer
from .services import answer


@extend_schema(request=SendChatRequestSerializer, responses={200: SendChatResponseSerializer})
@api_view(["POST"])
def send_chat_message(request):
    """
    POST /api/chat/send
    One chat message for the application selected in the chat page's MCP server picker
    (API_chat.md #1). The application is looked up by its publicId.
    """
    serializer = SendChatRequestSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({"error": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
    data = serializer.validated_data

    public_id = data["application"]["publicId"]
    try:
        project = Project.objects.get(public_id=public_id)
    except Project.DoesNotExist:
        return Response({"error": f"Application '{public_id}' not found"}, status=status.HTTP_400_BAD_REQUEST)

    return Response(answer(project, data["message"]))
