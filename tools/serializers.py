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
