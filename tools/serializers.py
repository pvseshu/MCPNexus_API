from rest_framework import serializers
from .models import Tool, ToolParameter


class ToolParameterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ToolParameter
        fields = ["id", "name", "location", "data_type", "required", "description", "enum_values"]


class ToolDetailSerializer(serializers.ModelSerializer):
    parameters = ToolParameterSerializer(many=True, read_only=True)

    class Meta:
        model = Tool
        fields = [
            "id",
            "name",
            "http_method",
            "path",
            "description",
            "operation_id",
            "parameters",
        ]


class McpToolCardSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    displayName = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    sourceEndpoint = serializers.CharField()
    httpMethod = serializers.CharField()
    serverId = serializers.CharField()
    serverName = serializers.CharField()
    applicationId = serializers.CharField()
    applicationName = serializers.CharField()
    requiredPermission = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    isAiReady = serializers.BooleanField()
    aiReadinessScore = serializers.IntegerField()
    sampleInputsCount = serializers.IntegerField()
    sampleOutputsCount = serializers.IntegerField()
    lastUsed = serializers.DateTimeField(allow_null=True)
    callCount = serializers.IntegerField()


class ListMcpToolsResponseSerializer(serializers.Serializer):
    tools = McpToolCardSerializer(many=True)


class ExecuteToolRequestSerializer(serializers.Serializer):
    input = serializers.DictField()


class ExecuteToolRequestEchoSerializer(serializers.Serializer):
    method = serializers.CharField()
    url = serializers.CharField()


class ExecuteToolResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()
    httpStatus = serializers.IntegerField(allow_null=True)
    durationMs = serializers.IntegerField()
    request = ExecuteToolRequestEchoSerializer(allow_null=True)
    response = serializers.JSONField(allow_null=True)
    error = serializers.CharField(allow_null=True)
