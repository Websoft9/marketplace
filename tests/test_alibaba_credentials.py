from __future__ import annotations

import os
import unittest
from unittest import mock

from cli import alibaba_credentials


class AliyunCredentialTests(unittest.TestCase):
    def test_reads_aliyun_names(self):
        with mock.patch.dict(os.environ, {
            "ALIYUN_ACCESS_KEY_ID": "aliyun-id",
            "ALIYUN_ACCESS_KEY_SECRET": "aliyun-secret",
            "ALIYUN_SECURITY_TOKEN": "aliyun-token",
        }, clear=True):
            credentials = alibaba_credentials.read()
        self.assertEqual(credentials.access_key_id, "aliyun-id")
        self.assertEqual(credentials.access_key_secret, "aliyun-secret")
        self.assertEqual(credentials.security_token, "aliyun-token")

    def test_missing_values_are_empty(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            credentials = alibaba_credentials.read()
        self.assertEqual(credentials.access_key_id, "")
        self.assertEqual(credentials.access_key_secret, "")
        self.assertEqual(credentials.security_token, "")

    def test_hint_names_only_the_aliyun_variables(self):
        hint = alibaba_credentials.missing_hint()
        self.assertIn("ALIYUN_ACCESS_KEY_ID", hint)
        self.assertIn("ALIYUN_SECURITY_TOKEN", hint)
        self.assertNotIn("ALIBABA_CLOUD_", hint)


if __name__ == "__main__":
    unittest.main()
