"""Custom FastMCP middleware for handling request-scoped context."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Mapping as MappingType, Optional, Set

from fastmcp.server.middleware import (
    CallNext,
    Middleware,
    MiddlewareContext,
)
from mcp.types import CallToolRequestParams

from analytics_mcp import request_context

logger = logging.getLogger(__name__)

_AUTH_TOKEN_ENV_KEY = "auth_token"
_AUTH_TOKEN_ENV_VAR = "GOOGLE_ANALYTICS_MCP_AUTH_TOKEN"
_AUTH_TOKENS_ENV_VAR = "GOOGLE_ANALYTICS_MCP_AUTH_TOKENS"
_LEGACY_AUTH_TOKEN_ENV_VAR = "GOOGLE_ADS_MCP_AUTH_TOKEN"
_LEGACY_AUTH_TOKENS_ENV_VAR = "GOOGLE_ADS_MCP_AUTH_TOKENS"
_GATEWAY_SECRET_ENV_VAR = "VEYLAN_GATEWAY_SIGNING_SECRET"
_GATEWAY_HEADER_ENV_VAR = "VEYLAN_GATEWAY_HEADER_NAME"
_GATEWAY_ALLOWED_SERVICES_ENV_VAR = "VEYLAN_GATEWAY_ALLOWED_SERVICES"
_DEFAULT_GATEWAY_HEADER = "X-Veylan-Gateway"


def _load_allowed_tokens() -> Optional[Set[str]]:
    """Returns the configured set of allowed auth tokens, if any."""
    raw_tokens: list[str] = []

    for env_var in (_AUTH_TOKENS_ENV_VAR, _LEGACY_AUTH_TOKENS_ENV_VAR):
        multi = os.environ.get(env_var)
        if multi:
            raw_tokens.extend(token.strip() for token in multi.split(","))

    for env_var in (_AUTH_TOKEN_ENV_VAR, _LEGACY_AUTH_TOKEN_ENV_VAR):
        single = os.environ.get(env_var)
        if single:
            raw_tokens.append(single.strip())

    tokens = {token for token in raw_tokens if token}
    return tokens or None


def _get_fastmcp_request(
    context: MiddlewareContext[CallToolRequestParams],
):
    fastmcp_context = getattr(context, "fastmcp_context", None)
    if fastmcp_context is None:
        return None

    try:
        return fastmcp_context.request_context.request
    except ValueError:
        return None


def _extract_header_token(
    context: MiddlewareContext[CallToolRequestParams],
) -> Optional[str]:
    """Attempts to read a Bearer token from the Authorization header."""
    request = _get_fastmcp_request(context)
    if request is None:
        return None

    headers = getattr(request, "headers", None)
    if headers is None:
        return None

    auth_header = headers.get("Authorization")
    if not auth_header:
        return None

    scheme, _, value = auth_header.partition(" ")
    if scheme.lower() != "bearer":
        return None

    return value.strip() or None


def _authorize_token(token: Optional[str]) -> None:
    """Validates the provided token against the configured allow list."""
    allowed_tokens = _load_allowed_tokens()
    if not allowed_tokens:
        # No tokens configured; treat as open access (development mode).
        return

    if token is None:
        raise PermissionError("Unauthorized: missing auth token.")

    if token not in allowed_tokens:
        raise PermissionError("Unauthorized: invalid auth token.")


def _gateway_header_name() -> str:
    override = os.environ.get(_GATEWAY_HEADER_ENV_VAR)
    if override and override.strip():
        return override.strip()
    return _DEFAULT_GATEWAY_HEADER


def _gateway_secret() -> Optional[str]:
    secret = os.environ.get(_GATEWAY_SECRET_ENV_VAR)
    if not secret:
        return None
    secret = secret.strip()
    return secret or None


def _load_allowed_gateway_services() -> Optional[Set[str]]:
    raw = os.environ.get(_GATEWAY_ALLOWED_SERVICES_ENV_VAR)
    if not raw:
        return None
    services = {entry.strip() for entry in raw.split(",") if entry.strip()}
    return services or None


def _extract_gateway_header(
    context: MiddlewareContext[CallToolRequestParams],
) -> Optional[str]:
    """Retrieves the gateway header value from the inbound request."""
    request = _get_fastmcp_request(context)
    if request is None:
        return None

    headers = getattr(request, "headers", None)
    if headers is None:
        return None

    header_name = _gateway_header_name()
    value = headers.get(header_name)
    if value:
        stripped = value.strip()
        return stripped or None

    items_accessor = getattr(headers, "items", None)
    if callable(items_accessor):
        header_name_lower = header_name.lower()
        for key, header_value in items_accessor():
            if key.lower() == header_name_lower and header_value:
                stripped = header_value.strip()
                if stripped:
                    return stripped

    return None


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(f"{data}{padding}".encode("ascii"))
    except (binascii.Error, UnicodeEncodeError) as exc:
        raise ValueError("Gateway header is not valid base64.") from exc


def _parse_gateway_header(value: str) -> tuple[bytes, dict[str, Any], bytes]:
    prefix, _, remainder = value.strip().partition(" ")
    if prefix.lower() != "v1" or not remainder:
        raise ValueError("Unsupported gateway signature version.")

    payload_b64, separator, signature_b64 = remainder.partition(".")
    if separator != "." or not payload_b64 or not signature_b64:
        raise ValueError("Gateway header is malformed.")

    payload_bytes = _b64url_decode(payload_b64)
    signature_bytes = _b64url_decode(signature_b64)

    try:
        payload: Any = json.loads(payload_bytes.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Gateway header payload is not valid JSON.") from exc

    if not isinstance(payload, dict):
        raise ValueError("Gateway header payload must be an object.")

    return payload_bytes, payload, signature_bytes


def _validate_gateway_header(context: MiddlewareContext[CallToolRequestParams]) -> None:
    """Validates the gateway-issued authentication header if configured."""
    secret = _gateway_secret()
    if not secret:
        return

    header_name = _gateway_header_name()
    header_value = _extract_gateway_header(context)
    if not header_value:
        logger.warning("gateway_header_missing header=%s", header_name)
        raise PermissionError("Unauthorized: missing gateway signature.")

    try:
        payload_bytes, payload, signature = _parse_gateway_header(header_value)
    except ValueError as exc:
        logger.warning("gateway_header_invalid header=%s reason=%s", header_name, exc)
        raise PermissionError("Unauthorized: invalid gateway signature.") from exc

    expected_signature = hmac.new(
        secret.encode("utf-8"), payload_bytes, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        logger.warning(
            "gateway_header_invalid header=%s reason=signature_mismatch", header_name
        )
        raise PermissionError("Unauthorized: invalid gateway signature.")

    allowed_services = _load_allowed_gateway_services()
    if allowed_services:
        service = payload.get("service")
        normalized_service = service.strip() if isinstance(service, str) else None
        if normalized_service not in allowed_services:
            allowed_str = ",".join(sorted(allowed_services))
            logger.warning(
                "gateway_service_mismatch header=%s service=%s allowed=%s",
                header_name,
                normalized_service,
                allowed_str,
            )
            raise PermissionError("Unauthorized: gateway service is not allowed.")


class RequestEnvironmentMiddleware(Middleware):
    """Stores per-request environment payloads in a context variable."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, Any],
    ) -> Any:
        # FastMCP strips the environment field during deserialization, so we need
        # to parse it from the raw request body ourselves.
        raw_environment = None
        if context.fastmcp_context:
            try:
                request = context.fastmcp_context.request_context.request
                if hasattr(request, "_body") and request._body:
                    body_json = json.loads(request._body.decode("utf-8"))
                    params = body_json.get("params", {})
                    raw_environment = params.get("environment")
            except Exception as e:
                logger.warning(
                    f"Failed to extract environment from raw request body: {e}",
                    exc_info=True,
                )

        sanitized_environment: MappingType[str, Any] | None = None
        token_from_environment: Optional[str] = None

        if isinstance(raw_environment, Mapping):
            mutable_environment = dict(raw_environment)
            raw_token = mutable_environment.pop(_AUTH_TOKEN_ENV_KEY, None)
            if isinstance(raw_token, str):
                token_from_environment = raw_token.strip() or None
            sanitized_environment = mutable_environment

        token = token_from_environment or _extract_header_token(context)
        _authorize_token(token)
        _validate_gateway_header(context)

        with request_context.use_request_environment(sanitized_environment):
            return await call_next(context)
