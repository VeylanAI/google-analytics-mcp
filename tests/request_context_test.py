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

"""Tests for request-scoped environment helpers."""

import unittest

from analytics_mcp import request_context


class RequestContextTest(unittest.TestCase):
    """Validates request-scoped environment helpers."""

    def setUp(self):
        request_context.clear_request_environment()

    def test_set_and_get_environment(self):
        environment = {
            "developer_token": "dev-token",
            "login_customer_id": "1234567890",
        }

        request_context.set_request_environment(environment)
        stored = request_context.get_request_environment()

        self.assertIsNotNone(stored)
        self.assertEqual(environment, stored)
        # Returned mapping should be a copy so callers cannot mutate shared state.
        stored["developer_token"] = "modified"
        self.assertEqual(
            "dev-token", request_context.get_environment_value("developer_token")
        )

    def test_clear_removes_environment(self):
        request_context.set_request_environment({"developer_token": "dev-token"})
        request_context.clear_request_environment()
        self.assertIsNone(request_context.get_request_environment())

    def test_context_manager_resets_environment(self):
        environment = {"developer_token": "dev-token"}
        with request_context.use_request_environment(environment):
            self.assertEqual(environment, request_context.get_request_environment())

        self.assertIsNone(request_context.get_request_environment())

    def test_nested_context_managers_restore_previous_state(self):
        outer = {"developer_token": "outer-token"}
        inner = {"developer_token": "inner-token"}

        with request_context.use_request_environment(outer):
            self.assertEqual(
                "outer-token",
                request_context.get_environment_value("developer_token"),
            )
            with request_context.use_request_environment(inner):
                self.assertEqual(
                    "inner-token",
                    request_context.get_environment_value("developer_token"),
                )

            self.assertEqual(
                "outer-token",
                request_context.get_environment_value("developer_token"),
            )


if __name__ == "__main__":
    unittest.main()
