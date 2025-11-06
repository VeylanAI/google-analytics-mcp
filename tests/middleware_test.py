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

"""Tests for custom FastMCP middleware."""

from __future__ import annotations

import json
import os
import unittest
from typing import Any
from unittest import mock

from fastmcp.server.middleware import MiddlewareContext
from mcp.types import CallToolRequestParams

from analytics_mcp import middleware, request_context


class RequestEnvironmentMiddlewareTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        request_context.clear_request_environment()
        self.middleware = middleware.RequestEnvironmentMiddleware()
        self.addAsyncCleanup(request_context.clear_request_environment)

    async def test_environment_is_stored_and_cleared(self):
        with mock.patch.dict(
            os.environ, {"GOOGLE_ADS_MCP_AUTH_TOKENS": "shared-secret"}, clear=False
        ):
            params = CallToolRequestParams(
                name="test",
                arguments={"arg": "value"},
            )

            # Mock the raw request body like FastMCP provides
            request_body = {
                "jsonrpc": "2.0",
                "id": "test-id",
                "method": "tools/call",
                "params": {
                    "name": "test",
                    "arguments": {"arg": "value"},
                    "environment": {
                        "developer_token": "token",
                        "auth_token": "shared-secret",
                    },
                },
            }

            fake_request = mock.Mock()
            fake_request._body = json.dumps(request_body).encode("utf-8")
            fake_request.headers = {}

            fake_request_context = mock.Mock()
            fake_request_context.request = fake_request

            fake_fastmcp_context = mock.Mock()
            fake_fastmcp_context.request_context = fake_request_context

            context = MiddlewareContext(
                message=params,
                method="tools/call",
                type="request",
                fastmcp_context=fake_fastmcp_context,
            )

            async def call_next(inner_context: MiddlewareContext[Any]) -> str:
                self.assertEqual(
                    "token",
                    request_context.get_environment_value("developer_token"),
                )
                self.assertIsNone(request_context.get_environment_value("auth_token"))
                self.assertIs(inner_context, context)
                return "ok"

            result = await self.middleware.on_call_tool(context, call_next)

        self.assertEqual("ok", result)
        self.assertIsNone(request_context.get_request_environment())

    async def test_missing_token_raises_error(self):
        with mock.patch.dict(
            os.environ, {"GOOGLE_ADS_MCP_AUTH_TOKENS": "shared-secret"}, clear=False
        ):
            params = CallToolRequestParams(
                name="test",
                arguments={},
            )

            # Mock the raw request body without auth token
            request_body = {
                "jsonrpc": "2.0",
                "id": "test-id",
                "method": "tools/call",
                "params": {
                    "name": "test",
                    "arguments": {},
                    "environment": {},
                },
            }

            fake_request = mock.Mock()
            fake_request._body = json.dumps(request_body).encode("utf-8")
            fake_request.headers = {}

            fake_request_context = mock.Mock()
            fake_request_context.request = fake_request

            fake_fastmcp_context = mock.Mock()
            fake_fastmcp_context.request_context = fake_request_context

            context = MiddlewareContext(
                message=params,
                method="tools/call",
                type="request",
                fastmcp_context=fake_fastmcp_context,
            )

            async def call_next(inner_context: MiddlewareContext[Any]) -> str:
                return "ok"

            with self.assertRaises(PermissionError):
                await self.middleware.on_call_tool(context, call_next)

    async def test_invalid_token_raises_error(self):
        with mock.patch.dict(
            os.environ, {"GOOGLE_ADS_MCP_AUTH_TOKENS": "shared-secret"}, clear=False
        ):
            params = CallToolRequestParams(
                name="test",
                arguments={},
            )

            # Mock the raw request body with bad token
            request_body = {
                "jsonrpc": "2.0",
                "id": "test-id",
                "method": "tools/call",
                "params": {
                    "name": "test",
                    "arguments": {},
                    "environment": {"auth_token": "bad-token"},
                },
            }

            fake_request = mock.Mock()
            fake_request._body = json.dumps(request_body).encode("utf-8")
            fake_request.headers = {}

            fake_request_context = mock.Mock()
            fake_request_context.request = fake_request

            fake_fastmcp_context = mock.Mock()
            fake_fastmcp_context.request_context = fake_request_context

            context = MiddlewareContext(
                message=params,
                method="tools/call",
                type="request",
                fastmcp_context=fake_fastmcp_context,
            )

            async def call_next(inner_context: MiddlewareContext[Any]) -> str:
                return "ok"

            with self.assertRaises(PermissionError):
                await self.middleware.on_call_tool(context, call_next)

    async def test_authorization_header_is_respected(self):
        with mock.patch.dict(
            os.environ, {"GOOGLE_ADS_MCP_AUTH_TOKENS": "shared-secret"}, clear=False
        ):
            params = CallToolRequestParams(
                name="test",
                arguments={},
            )

            # Mock the raw request body without auth token in environment
            request_body = {
                "jsonrpc": "2.0",
                "id": "test-id",
                "method": "tools/call",
                "params": {
                    "name": "test",
                    "arguments": {},
                    "environment": {},
                },
            }

            fake_request = mock.Mock()
            fake_request._body = json.dumps(request_body).encode("utf-8")
            fake_request.headers = {"Authorization": "Bearer shared-secret"}

            fake_request_context = mock.Mock()
            fake_request_context.request = fake_request

            fake_fastmcp_context = mock.Mock()
            fake_fastmcp_context.request_context = fake_request_context

            context = MiddlewareContext(
                message=params,
                method="tools/call",
                type="request",
                fastmcp_context=fake_fastmcp_context,
            )

            async def call_next(inner_context: MiddlewareContext[Any]) -> str:
                self.assertIsNone(request_context.get_environment_value("auth_token"))
                return "ok"

            result = await self.middleware.on_call_tool(context, call_next)

        self.assertEqual("ok", result)


if __name__ == "__main__":
    unittest.main()
