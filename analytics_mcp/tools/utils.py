# Copyright 2025 Google LLC All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Common utilities used by the MCP server."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, TypeVar

from analytics_mcp import request_context

from google.analytics import admin_v1alpha, admin_v1beta, data_v1beta
from google.api_core.gapic_v1.client_info import ClientInfo
from importlib import metadata
import google.auth
import proto

ClientT = TypeVar("ClientT")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


@dataclass(frozen=True)
class _ClientSpec:
    google_project_id: Optional[str]
    credential_fingerprint: str


@dataclass
class _ClientCache:
    spec: _ClientSpec
    admin_client: Optional[admin_v1beta.AnalyticsAdminServiceAsyncClient] = None
    data_client: Optional[data_v1beta.BetaAnalyticsDataAsyncClient] = None
    admin_alpha_client: Optional[admin_v1alpha.AnalyticsAdminServiceAsyncClient] = None


_CLIENT_CACHE: ContextVar[Optional[_ClientCache]] = ContextVar(
    "analytics_mcp_client_cache", default=None
)


class _RefreshTokenProxy:
    """Adapter to expose a callable _refresh_token for user ADCs."""

    __slots__ = ("_delegate",)

    def __init__(self, delegate: google.auth.credentials.Credentials) -> None:
        object.__setattr__(self, "_delegate", delegate)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._delegate, name, value)

    def __delattr__(self, name: str) -> None:
        delattr(self._delegate, name)

    def _refresh_token(self, request: Any) -> None:
        # Authorized user credentials only implement refresh(), so forward the call.
        self._delegate.refresh(request)


def _current_environment() -> Mapping[str, Any] | None:
    return request_context.get_request_environment()


def _get_google_project_id(
    environment: Mapping[str, Any] | None,
) -> Optional[str]:
    """Returns Google Cloud project id from request environment or process env."""
    if environment:
        google_project_id = environment.get("google_project_id") or environment.get(
            "project_id"
        )
        if isinstance(google_project_id, str) and google_project_id.strip():
            return google_project_id.strip()

    for env_var in ("GOOGLE_PROJECT_ID", "GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT"):
        value = os.environ.get(env_var)
        if value:
            value = value.strip()
            if value:
                return value

    return None


def _load_adc_dict(environment: Mapping[str, Any] | None) -> Optional[dict[str, Any]]:
    """Load ADC payload from request"""
    if not environment:
        return None

    raw_adc = environment.get("adc")
    if isinstance(raw_adc, Mapping):
        return dict(raw_adc)

    raw_adc_json = environment.get("adc_json")
    if isinstance(raw_adc_json, str):
        try:
            return json.loads(raw_adc_json)
        except json.JSONDecodeError:
            logger.error(
                "adc_json payload could not be parsed; falling back to local default credentials"
            )

    return None


def _get_package_version_with_fallback():
    """Returns the version of the package.

    Falls back to 'unknown' if the version can't be resolved.
    """
    try:
        return metadata.version("analytics-mcp")
    except metadata.PackageNotFoundError:
        return "unknown"
    except Exception as exc:  # pragma: no cover - defensive path
        logger.debug("Unable to determine analytics-mcp package version: %s", exc)
        return "unknown"


# Client information that adds a custom user agent to all API requests.
_CLIENT_INFO = ClientInfo(
    user_agent=f"analytics-mcp/{_get_package_version_with_fallback()}"
)

# Read-only scope for Analytics Admin API and Analytics Data API.
_READ_ONLY_ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def _ensure_impersonated_refresh_compat(
    credentials: google.auth.credentials.Credentials,
) -> google.auth.credentials.Credentials:
    """Wrap impersonated credentials so refresh flows handle user ADCs."""
    try:
        from google.auth import impersonated_credentials  # type: ignore
    except ImportError:
        return credentials

    if not isinstance(credentials, impersonated_credentials.Credentials):
        return credentials

    source_credentials = getattr(credentials, "_source_credentials", None)
    if source_credentials is None:
        return credentials

    refresh_attr = getattr(source_credentials, "_refresh_token", None)
    if isinstance(refresh_attr, str) and hasattr(source_credentials, "refresh"):
        credentials._source_credentials = _RefreshTokenProxy(source_credentials)  # type: ignore[attr-defined]

    return credentials


def _create_credentials(
    environment: Mapping[str, Any] | None = None,
) -> google.auth.credentials.Credentials:
    """Build Google credentials from the request environment or process defaults."""
    if environment is None:
        environment = _current_environment()

    adc_payload = _load_adc_dict(environment)
    quota_project_id = _get_google_project_id(environment)

    if adc_payload:
        credentials, project_id = google.auth.load_credentials_from_dict(
            adc_payload, scopes=[_READ_ONLY_ANALYTICS_SCOPE]
        )
    else:
        credentials, project_id = google.auth.default(
            scopes=[_READ_ONLY_ANALYTICS_SCOPE]
        )

    quota_project_id = quota_project_id or project_id

    if quota_project_id and hasattr(credentials, "with_quota_project"):
        try:
            credentials = credentials.with_quota_project(quota_project_id)
        except Exception as exc:
            logger.debug(
                "Unable to attach quota project %s to credentials: %s",
                quota_project_id,
                exc,
            )

    return _ensure_impersonated_refresh_compat(credentials)


def _credentials_fingerprint(environment: Mapping[str, Any] | None) -> str:
    """Returns a fingerprint representing the current credential source."""
    adc_payload = _load_adc_dict(environment)
    if adc_payload is not None:
        serialized = json.dumps(adc_payload, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    env_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if env_path:
        return f"path:{env_path}"

    return "default-adc"


def _current_client_spec(environment: Mapping[str, Any] | None) -> _ClientSpec:
    return _ClientSpec(
        google_project_id=_get_google_project_id(environment),
        credential_fingerprint=_credentials_fingerprint(environment),
    )


def _get_or_create_client_cache(environment: Mapping[str, Any] | None) -> _ClientCache:
    spec = _current_client_spec(environment)
    cache = _CLIENT_CACHE.get()
    if cache is None or cache.spec != spec:
        cache = _ClientCache(spec=spec)
        _CLIENT_CACHE.set(cache)
    return cache


def _get_cached_client(
    cache_attribute: str,
    builder: Callable[[Mapping[str, Any] | None], ClientT],
) -> ClientT:
    """Returns a cached API client for the current request context."""
    environment = _current_environment()
    cache = _get_or_create_client_cache(environment)

    client: Optional[ClientT] = getattr(cache, cache_attribute)
    if client is None:
        client = builder(environment)
        setattr(cache, cache_attribute, client)
    return client


def _build_admin_api_client(
    environment: Mapping[str, Any] | None,
) -> admin_v1beta.AnalyticsAdminServiceAsyncClient:
    credentials = _create_credentials(environment)
    return admin_v1beta.AnalyticsAdminServiceAsyncClient(
        client_info=_CLIENT_INFO,
        credentials=credentials,
    )


def _build_data_api_client(
    environment: Mapping[str, Any] | None,
) -> data_v1beta.BetaAnalyticsDataAsyncClient:
    credentials = _create_credentials(environment)
    return data_v1beta.BetaAnalyticsDataAsyncClient(
        client_info=_CLIENT_INFO,
        credentials=credentials,
    )


def _build_admin_alpha_api_client(
    environment: Mapping[str, Any] | None,
) -> admin_v1alpha.AnalyticsAdminServiceAsyncClient:
    credentials = _create_credentials(environment)
    return admin_v1alpha.AnalyticsAdminServiceAsyncClient(
        client_info=_CLIENT_INFO,
        credentials=credentials,
    )


def create_admin_api_client() -> admin_v1beta.AnalyticsAdminServiceAsyncClient:
    """Returns a properly configured Google Analytics Admin API async client.

    Uses request-scoped credentials when available; otherwise falls back to
    Application Default credentials with read-only scope.
    """
    return _get_cached_client("admin_client", _build_admin_api_client)


def create_data_api_client() -> data_v1beta.BetaAnalyticsDataAsyncClient:
    """Returns a properly configured Google Analytics Data API async client.

    Uses request-scoped credentials when available; otherwise falls back to
    Application Default credentials with read-only scope.
    """
    return _get_cached_client("data_client", _build_data_api_client)


def create_admin_alpha_api_client() -> admin_v1alpha.AnalyticsAdminServiceAsyncClient:
    """Returns a properly configured Google Analytics Admin API (alpha) async client.
    Uses request-scoped credentials when available; otherwise falls back to
    Application Default credentials with read-only scope.
    """
    return _get_cached_client("admin_alpha_client", _build_admin_alpha_api_client)


def construct_property_rn(property_value: int | str) -> str:
    """Returns a property resource name in the format required by APIs."""
    property_num = None
    if isinstance(property_value, int):
        property_num = property_value
    elif isinstance(property_value, str):
        property_value = property_value.strip()
        if property_value.isdigit():
            property_num = int(property_value)
        elif property_value.startswith("properties/"):
            numeric_part = property_value.split("/")[-1]
            if numeric_part.isdigit():
                property_num = int(numeric_part)
    if property_num is None:
        raise ValueError(
            (
                f"Invalid property ID: {property_value}. "
                "A valid property value is either a number or a string starting "
                "with 'properties/' and followed by a number."
            )
        )

    return f"properties/{property_num}"


def proto_to_dict(obj: proto.Message) -> Dict[str, Any]:
    """Converts a proto message to a dictionary."""
    return type(obj).to_dict(
        obj, use_integers_for_enums=False, preserving_proto_field_name=True
    )


def proto_to_json(obj: proto.Message) -> str:
    """Converts a proto message to a JSON string."""
    return type(obj).to_json(obj, indent=None, preserving_proto_field_name=True)
