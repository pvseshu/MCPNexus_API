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


class DiscoveredEndpointSerializer(serializers.Serializer):
    id = serializers.CharField()
    endpoint = serializers.CharField()
    method = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    tag = serializers.CharField(allow_blank=True)
    suggestedToolName = serializers.CharField()
    enabledForMcp = serializers.BooleanField()
    parametersCount = serializers.IntegerField()
    applicationId = serializers.CharField()
    applicationName = serializers.CharField()
    serverId = serializers.CharField()
    serverName = serializers.CharField()


class ListDiscoveredEndpointsResponseSerializer(serializers.Serializer):
    endpoints = DiscoveredEndpointSerializer(many=True)


SAMPLE_OUTPUT_TYPES = ["success", "empty", "validation_error", "auth_error", "business_error"]


def validate_samples(value, kind):
    """Checks a whole sample list. Errors read like "Sample 2: payload must be a JSON object."."""
    if not isinstance(value, list):
        raise serializers.ValidationError("Must be a list.")
    errors = []
    for i, sample in enumerate(value, start=1):
        label = f"Sample {i}"
        if not isinstance(sample, dict):
            errors.append(f"{label}: must be an object.")
            continue
        if not isinstance(sample.get("name"), str) or not sample["name"].strip():
            errors.append(f"{label}: name is required.")
        if not isinstance(sample.get("payload"), dict):
            errors.append(f"{label}: payload must be a JSON object.")
        if sample.get("description") is not None and not isinstance(sample["description"], str):
            errors.append(f"{label}: description must be a string.")
        allowed = ["success"] if kind == "input" else SAMPLE_OUTPUT_TYPES
        if sample.get("type") is not None and sample["type"] not in allowed:
            errors.append(f"{label}: type must be one of {', '.join(allowed)}.")
    if errors:
        raise serializers.ValidationError(errors)
    return value


class UpdateToolRequestSerializer(serializers.Serializer):
    status = serializers.ChoiceField(required=False, choices=["Active", "Disabled"])
    description = serializers.CharField(required=False, allow_blank=False)
    requiredPermission = serializers.CharField(required=False, allow_blank=True, max_length=200)
    whenToUse = serializers.CharField(required=False, allow_blank=True)
    whenNotToUse = serializers.CharField(required=False, allow_blank=True)
    callSequence = serializers.CharField(required=False, allow_blank=True)
    sampleInputs = serializers.JSONField(required=False)
    sampleOutputs = serializers.JSONField(required=False)

    def validate_sampleInputs(self, value):
        return validate_samples(value, "input")

    def validate_sampleOutputs(self, value):
        return validate_samples(value, "output")


class ToolInputSerializer(serializers.Serializer):
    name = serializers.CharField()
    location = serializers.CharField()
    type = serializers.CharField()
    required = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
    exampleValue = serializers.CharField(allow_blank=True)


class ToolSampleSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField(allow_blank=True)
    type = serializers.CharField()
    payload = serializers.JSONField()


class ToolDetailPayloadSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    displayName = serializers.CharField()
    sourceEndpoint = serializers.CharField()
    httpMethod = serializers.CharField()
    serverId = serializers.CharField()
    serverName = serializers.CharField()
    applicationId = serializers.CharField()
    applicationName = serializers.CharField()
    status = serializers.CharField()
    isAiReady = serializers.BooleanField()
    aiReadinessScore = serializers.IntegerField()
    description = serializers.CharField(allow_blank=True)
    requiredPermission = serializers.CharField(allow_blank=True)
    whenToUse = serializers.CharField(allow_blank=True)
    whenNotToUse = serializers.CharField(allow_blank=True)
    callSequence = serializers.CharField(allow_blank=True)
    outputSchemaDescription = serializers.CharField(allow_blank=True)
    inputs = ToolInputSerializer(many=True)
    sampleInputs = ToolSampleSerializer(many=True)
    sampleOutputs = ToolSampleSerializer(many=True)


class ToolDetailResponseSerializer(serializers.Serializer):
    tool = ToolDetailPayloadSerializer()


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
