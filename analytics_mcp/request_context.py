#!/usr/bin/env python

# Copyright 2025 Google LLC.
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

"""Helpers for storing per-request environment payloads."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Dict, Iterable, Mapping, Optional, TypedDict


class RequestEnvironment(TypedDict, total=False):
    """Typed representation of the expected request environment payload."""

    developer_token: str
    login_customer_id: str
    refresh_token: str
    client_id: str
    client_secret: str
    impersonated_user_email: str
    customer_id: str
    adc_json: str


_REQUEST_ENV: ContextVar[Optional[RequestEnvironment]] = ContextVar(
    "ads_mcp_request_environment", default=None
)


def _normalize_environment(
    environment: Optional[Mapping[str, Any]],
) -> Optional[RequestEnvironment]:
    if environment is None:
        return None

    # Copy into a plain dict so downstream consumers are insulated from mutating
    # caller-provided mappings.
    normalized: Dict[str, Any] = dict(environment)
    return normalized  # type: ignore[return-value]


def set_request_environment(environment: Optional[Mapping[str, Any]]) -> Token:
    """Sets the current request environment payload.

    Returns the context token so callers can manually reset the value if they
    manage the ContextVar lifecycle directly.
    """
    return _REQUEST_ENV.set(_normalize_environment(environment))


def clear_request_environment() -> None:
    """Clears any request environment payload stored in the current context."""
    _REQUEST_ENV.set(None)


def get_request_environment() -> Optional[RequestEnvironment]:
    """Retrieves the active request environment payload, if one exists."""
    environment = _REQUEST_ENV.get()
    if environment is None:
        return None
    # Return a shallow copy so consumers cannot mutate shared state.
    return dict(environment)


def get_environment_value(key: str, default: Any = None) -> Any:
    """Convenience accessor for a value inside the current request environment."""
    environment = _REQUEST_ENV.get()
    if environment is None:
        return default
    return environment.get(key, default)


@contextmanager
def use_request_environment(environment: Optional[Mapping[str, Any]]) -> Iterable[None]:
    """Context manager that sets the request environment for the duration of a block."""
    token = set_request_environment(environment)
    try:
        yield
    finally:
        _REQUEST_ENV.reset(token)
