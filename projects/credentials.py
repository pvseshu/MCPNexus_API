"""Storing an application's auth config and turning it into a bearer token.

Secrets (AuthBlue service password, OAuth client secret, IDaaS secret) are encrypted
at rest inside `Project.auth_config` and are never returned by any endpoint.
"""
import base64
import hashlib
import json

import httpx
from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

TOKEN_TIMEOUT = 10.0
ENC_PREFIX = "enc:"

# auth type -> the field of that block that holds the secret
SECRET_FIELDS = {"authBlue": "servicePassword", "oauth": "clientSecret", "idaas": "secret"}


class TokenError(Exception):
    """The bearer token could not be obtained. The message is safe to show to the user."""


def _fernet():
    key = getattr(settings, "CREDENTIAL_ENCRYPTION_KEY", "") or settings.SECRET_KEY
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt_secret(value):
    return ENC_PREFIX + _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value):
    """Plaintext of a stored secret. Values without the prefix (e.g. straight from a request) pass through."""
    if not value or not value.startswith(ENC_PREFIX):
        return value or ""
    try:
        return _fernet().decrypt(value[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        raise TokenError("Stored credentials cannot be decrypted (encryption key changed). Re-save the application's auth settings.")


def prepare_for_storage(new_config, existing_config=None):
    """Copy of `new_config` with secrets encrypted.

    The UI never receives secrets, so a blank secret means "unchanged": the stored one is kept.
    """
    config = json.loads(json.dumps(new_config or {}))
    existing = existing_config or {}
    for block, field in SECRET_FIELDS.items():
        if not isinstance(config.get(block), dict):
            continue
        value = config[block].get(field) or ""
        if value:
            config[block][field] = value if value.startswith(ENC_PREFIX) else encrypt_secret(value)
        else:
            config[block][field] = (existing.get(block) or {}).get(field, "")
    return config


def mask_secrets(config):
    """Copy of the stored config that is safe to return."""
    config = json.loads(json.dumps(config or {}))
    for block, field in SECRET_FIELDS.items():
        if isinstance(config.get(block), dict):
            config[block][field] = ""
    return config


def _authblue_token(cfg):
    if not cfg.get("tokenUrl"):
        raise TokenError("AuthBlue token URL is not configured.")
    resp = httpx.post(
        cfg["tokenUrl"],
        json={
            "serviceId": cfg.get("serviceId"),
            "servicePassword": decrypt_secret(cfg.get("servicePassword")),
            "scopeGroups": cfg.get("scopeGroups", []),
        },
        timeout=TOKEN_TIMEOUT,
    )
    return resp


def _json_escape(value):
    return json.dumps(value)[1:-1]


def _oauth_token(cfg):
    if not cfg.get("tokenUrl"):
        raise TokenError("OAuth token URL is not configured.")
    client_id = cfg.get("clientId", "")
    client_secret = decrypt_secret(cfg.get("clientSecret"))
    if cfg.get("credentialStyle") == "json_body":
        template = cfg.get("requestBodyTemplate") or ""
        rendered = template.replace("{{clientId}}", _json_escape(client_id)).replace(
            "{{clientSecret}}", _json_escape(client_secret)
        )
        try:
            body = json.loads(rendered)
        except ValueError:
            raise TokenError("OAuth request body template is not valid JSON.")
        return httpx.post(cfg["tokenUrl"], json=body, timeout=TOKEN_TIMEOUT)
    return httpx.post(
        cfg["tokenUrl"],
        data={"grant_type": "client_credentials"},
        auth=(client_id, client_secret),
        timeout=TOKEN_TIMEOUT,
    )


TOKEN_FETCHERS = {"authblue": ("authBlue", _authblue_token), "oauth": ("oauth", _oauth_token)}


def fetch_token(auth_config):
    """Bearer token for an application's auth config, or None when the type needs no token.

    Raises TokenError (never leaking secrets) when a token is needed but cannot be obtained.
    """
    auth_type = (auth_config or {}).get("type")
    if auth_type not in TOKEN_FETCHERS:
        if auth_type == "idaas":
            raise TokenError("IDaaS authentication is not supported yet.")
        return None
    block, fetcher = TOKEN_FETCHERS[auth_type]
    cfg = auth_config.get(block)
    if not isinstance(cfg, dict):
        return None
    try:
        resp = fetcher(cfg)
        resp.raise_for_status()
        data = resp.json()
    except TokenError:
        raise
    except httpx.HTTPStatusError as e:
        raise TokenError(f"Token endpoint returned {e.response.status_code}.")
    except httpx.TimeoutException:
        raise TokenError("Token endpoint timed out.")
    except httpx.HTTPError:
        raise TokenError("Token endpoint could not be reached.")
    except ValueError:
        raise TokenError("Token endpoint did not return JSON.")
    token = data.get("access_token") or data.get("token") if isinstance(data, dict) else None
    if not token:
        raise TokenError("Token response has no access_token.")
    return token
