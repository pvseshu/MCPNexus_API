from rest_framework import serializers
from .models import Project
from tools.serializers import ToolDetailSerializer


class ProjectDetailSerializer(serializers.ModelSerializer):
    tools = ToolDetailSerializer(many=True, read_only=True)

    class Meta:
        model = Project
        fields = ["id", "name", "description", "openapi_url", "base_url", "status", "tools"]


class OnboardProjectRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    openapi_url = serializers.URLField()


class OnboardProjectResponseSerializer(serializers.Serializer):
    project_id = serializers.IntegerField()
    project_name = serializers.CharField()
    apis_found = serializers.IntegerField()
    tools_created = serializers.IntegerField()
