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


# --- App Registration wizard (API.md) -----------------------------------

class AuthBlueConfigSerializer(serializers.Serializer):
    tokenUrl = serializers.URLField()
    serviceId = serializers.CharField(max_length=200)
    servicePassword = serializers.CharField(max_length=500, allow_blank=True)
    scopeGroups = serializers.ListField(child=serializers.CharField(), required=False, default=list)


class AuthConfigSerializer(serializers.Serializer):
    type = serializers.CharField(max_length=50)
    authBlue = AuthBlueConfigSerializer(required=False)


class AnalyzeSpecRequestSerializer(serializers.Serializer):
    swaggerUrls = serializers.ListField(child=serializers.URLField(), min_length=1)
    authConfig = AuthConfigSerializer(required=False)


class DiscoveredApiParameterSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()
    required = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
    exampleValue = serializers.CharField(allow_blank=True, required=False)
    location = serializers.CharField(required=False)


class DiscoveredApiSerializer(serializers.Serializer):
    id = serializers.CharField()
    endpoint = serializers.CharField()
    method = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    tag = serializers.CharField(allow_blank=True)
    suggestedToolName = serializers.CharField()
    parameters = DiscoveredApiParameterSerializer(many=True)


class AnalyzeSpecResponseSerializer(serializers.Serializer):
    specVersion = serializers.CharField()
    baseUrl = serializers.CharField(allow_blank=True)
    totalApisDiscovered = serializers.IntegerField()
    tagGroups = serializers.ListField(child=serializers.CharField())
    apis = DiscoveredApiSerializer(many=True)


class ImportantTermSerializer(serializers.Serializer):
    term = serializers.CharField()
    definition = serializers.CharField(allow_blank=True)


class AiContextSerializer(serializers.Serializer):
    businessPurpose = serializers.CharField(allow_blank=True, required=False, default="")
    businessDomain = serializers.CharField(allow_blank=True, required=False, default="")
    keyUseCases = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    commonWorkflows = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    importantTerminology = ImportantTermSerializer(many=True, required=False, default=list)
    aiGuidance = serializers.CharField(allow_blank=True, required=False, default="")


class ApplicationInputSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    appCode = serializers.CharField(max_length=100)
    carId = serializers.CharField(max_length=100, allow_blank=True, required=False, default="")
    description = serializers.CharField(allow_blank=True, required=False, default="")
    owner = serializers.CharField(max_length=200, allow_blank=True, required=False, default="")
    ownerEmail = serializers.EmailField(allow_blank=True, required=False, default="")
    supportDL = serializers.EmailField(allow_blank=True, required=False, default="")
    department = serializers.CharField(max_length=200, allow_blank=True, required=False, default="")
    swaggerUrls = serializers.ListField(child=serializers.URLField(), min_length=1)
    authConfig = AuthConfigSerializer(required=False)
    aiContext = AiContextSerializer(required=False)


class SelectedApiSerializer(serializers.Serializer):
    id = serializers.CharField()
    endpoint = serializers.CharField()
    method = serializers.CharField()
    toolName = serializers.CharField(max_length=300)


class GenerateMcpServerRequestSerializer(serializers.Serializer):
    application = ApplicationInputSerializer()
    selectedApis = SelectedApiSerializer(many=True, allow_empty=False)


class ApplicationOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    appCode = serializers.CharField()
    carId = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    isAiReady = serializers.BooleanField()
    lastUpdated = serializers.DateTimeField()


class McpServerOutputSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    version = serializers.CharField()
    endpointUrl = serializers.CharField()
    transportType = serializers.CharField()
    healthStatus = serializers.CharField()


class McpToolOutputSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    displayName = serializers.CharField()
    sourceEndpoint = serializers.CharField()
    httpMethod = serializers.CharField()
    requiredPermission = serializers.CharField()
    status = serializers.CharField()


class GenerateMcpServerResponseSerializer(serializers.Serializer):
    application = ApplicationOutputSerializer()
    mcpServer = McpServerOutputSerializer()
    mcpTools = McpToolOutputSerializer(many=True)
