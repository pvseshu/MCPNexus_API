"""Django settings for MCP Nexus. Everything environment-driven via .env."""
import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env(key, default=None):
    return os.environ.get(key, default)


def env_bool(key, default=False):
    return str(env(key, str(default))).lower() in ("1", "true", "yes", "on")


# --- Core ---------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS", "*").split(",")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third party
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    # MCP Nexus apps
    "projects",
    "api_registry",
    "tools",
    "embeddings",
    "conversations",
    "orchestration",
    "auth_governance",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "mcp_nexus.urls"
WSGI_APPLICATION = "mcp_nexus.wsgi.application"
ASGI_APPLICATION = "mcp_nexus.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database -----------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", "mcpnexus"),
        "USER": env("POSTGRES_USER", "mcpnexus"),
        "PASSWORD": env("POSTGRES_PASSWORD", "mcpnexus"),
        "HOST": env("POSTGRES_HOST", "localhost"),
        "PORT": env("POSTGRES_PORT", "5432"),
    }
}

AUTH_USER_MODEL = "auth_governance.User"
AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- DRF + JWT ----------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.AllowAny",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SPECTACULAR_SETTINGS = {
    "TITLE": "MCP Nexus API",
    "DESCRIPTION": "API for onboarding external applications via OpenAPI specs and exposing them as MCP tools.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

SIMPLE_JWT = {
    "SIGNING_KEY": env("JWT_SECRET", SECRET_KEY),
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=12),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

CORS_ALLOW_ALL_ORIGINS = True

# --- Qdrant -------------------------------------------------------------
QDRANT_URL = env("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = env("QDRANT_API_KEY", "") or None
QDRANT_PROJECTS_COLLECTION = env("QDRANT_PROJECTS_COLLECTION", "mcp_projects")
QDRANT_TOOLS_COLLECTION = env("QDRANT_TOOLS_COLLECTION", "mcp_tools")

# --- Embeddings (served locally via Ollama, e.g. `ollama run bge-large`) ---
OLLAMA_URL = env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_EMBEDDING_MODEL = env("OLLAMA_EMBEDDING_MODEL", "bge-large")
EMBEDDING_DIM = int(env("EMBEDDING_DIM", "1024"))

# --- Chat (served locally via Ollama) ---
OLLAMA_CHAT_MODEL = env("OLLAMA_CHAT_MODEL", "llama3.1:8b-instruct-q4_k_m")

# --- OpenAI (GPT-5.2), used by the chat tool selection ---
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_BASE_URL = env("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL = env("OPENAI_MODEL", "gpt-5.2")
OPENAI_REASONING_EFFORT = env("OPENAI_REASONING_EFFORT", "low")  # none / low / medium / high

# --- Azure OpenAI (GPT-5.2) --------------------------------------------
AZURE_OPENAI_ENDPOINT = env("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = env("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_API_VERSION = env("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_OPENAI_DEPLOYMENT = env("AZURE_OPENAI_DEPLOYMENT", "gpt-5.2")

# Encrypts stored app credentials (auth_config secrets). Falls back to SECRET_KEY; changing
# either one makes previously saved secrets unreadable.
CREDENTIAL_ENCRYPTION_KEY = env("CREDENTIAL_ENCRYPTION_KEY", "")

# Spotify only: Run Test uses this bearer token instead of calling the token API.
SPOTIFY_BEARER_TOKEN = env("SPOTIFY_BEARER_TOKEN", "")

# --- MCP servers ---------------------------------------------------------
MCP_SERVER_BASE_URL = env("MCP_SERVER_BASE_URL", "http://localhost:8000/mcp")

# --- Orchestration tuning ----------------------------------------------
PROJECT_MATCH_THRESHOLD = float(env("PROJECT_MATCH_THRESHOLD", "0.45"))
TOOL_MATCH_THRESHOLD = float(env("TOOL_MATCH_THRESHOLD", "0.45"))
TOOL_CANDIDATE_COUNT = int(env("TOOL_CANDIDATE_COUNT", "5"))
MAX_TOOL_CHAIN_STEPS = int(env("MAX_TOOL_CHAIN_STEPS", "3"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", "INFO")},
}
