from rest_framework import serializers


class ChatMcpServerSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, required=False)
    name = serializers.CharField(allow_blank=True, required=False)


class ChatApplicationSerializer(serializers.Serializer):
    # publicId / appCode are only sent when the client knows them.
    publicId = serializers.CharField(max_length=100)
    name = serializers.CharField(allow_blank=True, required=False)
    appCode = serializers.CharField(allow_blank=True, required=False)


class SendChatRequestSerializer(serializers.Serializer):
    message = serializers.CharField(trim_whitespace=True)
    mcpServer = ChatMcpServerSerializer(required=False)
    application = ChatApplicationSerializer()


class SendChatResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["success", "error"])
    message = serializers.CharField()
    list = serializers.ListField(child=serializers.CharField())
