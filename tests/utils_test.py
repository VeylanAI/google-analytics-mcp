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

"""Test cases for the utils module."""

import unittest
from unittest import mock

from analytics_mcp import request_context
from analytics_mcp.tools import utils


class TestUtils(unittest.TestCase):
    """Test cases for the utils module."""

    def setUp(self):
        super().setUp()
        # Ensure each test starts with an empty client cache and request context.
        token = utils._CLIENT_CACHE.set(None)
        self.addCleanup(utils._CLIENT_CACHE.reset, token)
        request_context.clear_request_environment()
        self.addCleanup(request_context.clear_request_environment)

    def test_construct_property_rn(self):
        """Tests construct_property_rn using valid input."""
        self.assertEqual(
            utils.construct_property_rn(12345),
            "properties/12345",
            "Numeric property ID should b considered valid",
        )
        self.assertEqual(
            utils.construct_property_rn("12345"),
            "properties/12345",
            "Numeric property ID as string should be considered valid",
        )
        self.assertEqual(
            utils.construct_property_rn(" 12345  "),
            "properties/12345",
            "Whitespace around property ID should be considered valid",
        )
        self.assertEqual(
            utils.construct_property_rn("properties/12345"),
            "properties/12345",
            "Full resource name should be considered valid",
        )

    def test_construct_property_rn_invalid_input(self):
        """Tests that construct_property_rn raises a ValueError for invalid input."""
        with self.assertRaises(ValueError, msg="None should fail"):
            utils.construct_property_rn(None)
        with self.assertRaises(ValueError, msg="Empty string should fail"):
            utils.construct_property_rn("")
        with self.assertRaises(ValueError, msg="Non-numeric string should fail"):
            utils.construct_property_rn("abc")
        with self.assertRaises(ValueError, msg="Resource name without ID should fail"):
            utils.construct_property_rn("properties/")
        with self.assertRaises(
            ValueError, msg="Resource name with non-numeric ID should fail"
        ):
            utils.construct_property_rn("properties/abc")
        with self.assertRaises(
            ValueError,
            msg="Resource name with more than 2 components should fail",
        ):
            utils.construct_property_rn("properties/123/abc")

    def test_create_admin_client_prefers_request_adc_credentials(self):
        """Request-scoped ADC payload should drive credential construction."""
        adc_payload = {"type": "service_account", "private_key_id": "abc123"}
        base_credentials = mock.Mock()
        scoped_credentials = mock.Mock()
        base_credentials.with_quota_project.return_value = scoped_credentials

        with (
            mock.patch(
                "analytics_mcp.tools.utils.google.auth.load_credentials_from_dict",
                return_value=(base_credentials, "adc-project"),
            ) as load_mock,
            mock.patch("analytics_mcp.tools.utils.google.auth.default") as default_mock,
            mock.patch.object(
                utils.admin_v1beta, "AnalyticsAdminServiceAsyncClient", autospec=True
            ) as admin_client_cls,
        ):
            admin_client_instance = mock.Mock()
            admin_client_cls.return_value = admin_client_instance

            environment = {
                "adc": adc_payload,
                "google_project_id": "request-project",
            }
            with request_context.use_request_environment(environment):
                client = utils.create_admin_api_client()

        self.assertIs(client, admin_client_instance)
        load_mock.assert_called_once()
        self.assertEqual(load_mock.call_args.args[0], adc_payload)
        self.assertEqual(
            load_mock.call_args.kwargs["scopes"],
            [utils._READ_ONLY_ANALYTICS_SCOPE],
        )
        default_mock.assert_not_called()
        base_credentials.with_quota_project.assert_called_once_with("request-project")
        self.assertIs(
            admin_client_cls.call_args.kwargs["credentials"], scoped_credentials
        )

    def test_create_admin_client_falls_back_to_default_credentials(self):
        """ADC defaults should be used when no request credentials arrive."""
        base_credentials = mock.Mock()
        scoped_credentials = mock.Mock()
        base_credentials.with_quota_project.return_value = scoped_credentials

        with (
            mock.patch(
                "analytics_mcp.tools.utils.google.auth.load_credentials_from_dict"
            ) as load_mock,
            mock.patch(
                "analytics_mcp.tools.utils.google.auth.default",
                return_value=(base_credentials, "default-project"),
            ) as default_mock,
            mock.patch.object(
                utils.admin_v1beta, "AnalyticsAdminServiceAsyncClient", autospec=True
            ) as admin_client_cls,
        ):
            admin_client_cls.return_value = mock.Mock()
            client = utils.create_admin_api_client()

        load_mock.assert_not_called()
        default_mock.assert_called_once_with(scopes=[utils._READ_ONLY_ANALYTICS_SCOPE])
        base_credentials.with_quota_project.assert_called_once_with("default-project")
        self.assertIs(
            admin_client_cls.call_args.kwargs["credentials"], scoped_credentials
        )
        self.assertIs(client, admin_client_cls.return_value)

    def test_create_admin_client_reuses_cache_with_same_credentials(self):
        """Client instances should be cached when credential fingerprint matches."""
        adc_payload = {"type": "service_account", "private_key_id": "first"}
        credentials = mock.Mock()
        credentials.with_quota_project.return_value = credentials

        with (
            mock.patch(
                "analytics_mcp.tools.utils.google.auth.load_credentials_from_dict",
                return_value=(credentials, "project-1"),
            ) as load_mock,
            mock.patch("analytics_mcp.tools.utils.google.auth.default") as default_mock,
            mock.patch.object(
                utils.admin_v1beta, "AnalyticsAdminServiceAsyncClient", autospec=True
            ) as admin_client_cls,
        ):
            client_instance = mock.Mock()
            admin_client_cls.return_value = client_instance

            with request_context.use_request_environment({"adc": adc_payload}):
                client_one = utils.create_admin_api_client()
                client_two = utils.create_admin_api_client()

        self.assertIs(client_one, client_instance)
        self.assertIs(client_two, client_instance)
        admin_client_cls.assert_called_once()
        load_mock.assert_called_once()
        default_mock.assert_not_called()

    def test_create_admin_client_cache_invalidated_when_credentials_change(self):
        """Changing request credentials should result in a new cached client."""
        adc_payload_one = {"type": "service_account", "private_key_id": "one"}
        adc_payload_two = {"type": "service_account", "private_key_id": "two"}
        credentials_one = mock.Mock()
        credentials_two = mock.Mock()
        credentials_one.with_quota_project.return_value = credentials_one
        credentials_two.with_quota_project.return_value = credentials_two

        with (
            mock.patch(
                "analytics_mcp.tools.utils.google.auth.load_credentials_from_dict",
                side_effect=[
                    (credentials_one, "project-1"),
                    (credentials_two, "project-2"),
                ],
            ) as load_mock,
            mock.patch("analytics_mcp.tools.utils.google.auth.default") as default_mock,
            mock.patch.object(
                utils.admin_v1beta, "AnalyticsAdminServiceAsyncClient", autospec=True
            ) as admin_client_cls,
        ):
            client_one = mock.Mock(name="client-one")
            client_two = mock.Mock(name="client-two")
            admin_client_cls.side_effect = [client_one, client_two]

            with request_context.use_request_environment({"adc": adc_payload_one}):
                result_one = utils.create_admin_api_client()

            with request_context.use_request_environment({"adc": adc_payload_two}):
                result_two = utils.create_admin_api_client()

        self.assertIs(result_one, client_one)
        self.assertIs(result_two, client_two)
        self.assertEqual(admin_client_cls.call_count, 2)
        self.assertEqual(load_mock.call_count, 2)
        default_mock.assert_not_called()
