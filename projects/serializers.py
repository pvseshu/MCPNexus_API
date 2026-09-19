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
    tokenUrl = serializers.CharField(allow_blank=True, required=False, default="")
    serviceId = serializers.CharField(max_length=200, allow_blank=True, required=False, default="")
    servicePassword = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")
    scopeGroups = serializers.ListField(child=serializers.CharField(allow_blank=True), required=False, default=list)


class GenericOAuthConfigSerializer(serializers.Serializer):
    tokenUrl = serializers.CharField(allow_blank=True, required=False, default="")
    clientId = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")
    clientSecret = serializers.CharField(max_length=500, allow_blank=True, required=False, default="")
    credentialStyle = serializers.ChoiceField(choices=["basic_auth", "json_body"], required=False, default="basic_auth")
    requestBodyTemplate = serializers.CharField(allow_blank=True, required=False, default="")


class AuthConfigSerializer(serializers.Serializer):
    type = serializers.CharField(max_length=50)
    authBlue = AuthBlueConfigSerializer(required=False)
    oauth = GenericOAuthConfigSerializer(required=False)
    # Stored but not used yet.
    idaas = serializers.DictField(required=False)

    # Only the block of the selected type has to be complete; the UI sends the others as blank defaults.
    REQUIRED_FIELDS = {
        "authblue": ("authBlue", ["tokenUrl", "serviceId"]),
        "oauth": ("oauth", ["tokenUrl", "clientId"]),
    }

    def validate(self, attrs):
        # The UI sends [""] for an empty scope list.
        if "authBlue" in attrs:
            attrs["authBlue"]["scopeGroups"] = [g for g in attrs["authBlue"]["scopeGroups"] if g.strip()]
        if "scope" in attrs.get("idaas", {}):
            attrs["idaas"]["scope"] = [g for g in attrs["idaas"]["scope"] if not isinstance(g, str) or g.strip()]
        block, fields = self.REQUIRED_FIELDS.get(attrs["type"], (None, []))
        if block:
            values = attrs.get(block) or {}
            errors = {f: ["This field may not be blank."] for f in fields if not values.get(f)}
            if errors:
                raise serializers.ValidationError({block: errors})
        return attrs


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
    intendedConsumers = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    usageGuidelines = serializers.CharField(allow_blank=True, required=False, default="")
    restrictions = serializers.CharField(allow_blank=True, required=False, default="")
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
    publicId = serializers.CharField()
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


class NavigationCountsSerializer(serializers.Serializer):
    mcpServers = serializers.IntegerField()
    mcpTools = serializers.IntegerField()
    pendingAccessRequests = serializers.IntegerField()


class McpServerApplicationSerializer(serializers.Serializer):
    id = serializers.CharField()
    publicId = serializers.CharField()
    name = serializers.CharField()
    appCode = serializers.CharField()
    owner = serializers.CharField()
    ownerEmail = serializers.CharField()
    department = serializers.CharField()
    isAiReady = serializers.BooleanField()


class McpServerSummarySerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    version = serializers.CharField()
    status = serializers.CharField()
    healthStatus = serializers.CharField()
    endpointUrl = serializers.CharField()
    transportType = serializers.CharField()
    toolsCount = serializers.IntegerField()
    isPublishedToCatalog = serializers.BooleanField()
    lastDeployed = serializers.DateTimeField()
    usedByApps = serializers.ListField(child=serializers.CharField())
    dependsOnServers = serializers.ListField(child=serializers.CharField())
    application = McpServerApplicationSerializer()


class ListMcpServersResponseSerializer(serializers.Serializer):
    servers = McpServerSummarySerializer(many=True)


class CatalogVisibilityRequestSerializer(serializers.Serializer):
    isPublishedToCatalog = serializers.BooleanField()


class CatalogVisibilityResponseSerializer(serializers.Serializer):
    id = serializers.CharField()
    isPublishedToCatalog = serializers.BooleanField()


class McpServerApiParameterSerializer(serializers.Serializer):
    name = serializers.CharField()
    location = serializers.CharField()
    type = serializers.CharField()
    required = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True)
    exampleValue = serializers.CharField(allow_blank=True)


class McpServerApiSerializer(serializers.Serializer):
    id = serializers.CharField()
    endpoint = serializers.CharField()
    method = serializers.CharField()
    summary = serializers.CharField(allow_blank=True)
    description = serializers.CharField(allow_blank=True)
    tag = serializers.CharField()
    suggestedToolName = serializers.CharField()
    enabledForMcp = serializers.BooleanField()
    parameters = McpServerApiParameterSerializer(many=True)


class McpServerDetailResponseSerializer(serializers.Serializer):
    server = McpServerSummarySerializer()
    application = serializers.DictField()
    apis = McpServerApiSerializer(many=True)


class AiSummaryConfigSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    title = serializers.CharField(allow_blank=True, required=False, default="")
    instructions = serializers.CharField(allow_blank=True, required=False, default="")
    includedApiIds = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    sampleOutput = serializers.CharField(allow_blank=True, required=False, default="")


class UpdateMcpServerRequestSerializer(serializers.Serializer):
    """Every field is optional; only the ones sent are changed."""

    name = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(allow_blank=True, required=False)
    owner = serializers.CharField(max_length=200, required=False)
    ownerEmail = serializers.EmailField(allow_blank=True, required=False)
    supportDL = serializers.EmailField(allow_blank=True, required=False)
    department = serializers.CharField(max_length=200, allow_blank=True, required=False)
    status = serializers.ChoiceField(choices=["Active", "Maintenance", "Disabled"], required=False)
    swaggerUrls = serializers.ListField(child=serializers.URLField(), min_length=1, required=False)
    authConfig = AuthConfigSerializer(required=False)
    aiContext = AiContextSerializer(required=False)
    aiSummaryConfig = AiSummaryConfigSerializer(required=False)
