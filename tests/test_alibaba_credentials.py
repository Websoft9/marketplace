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

    def test_reads_international_names(self):
        with mock.patch.dict(os.environ, {
            "ALIBABA_CLOUD_ACCESS_KEY_ID": "international-id",
            "ALIBABA_CLOUD_ACCESS_KEY_SECRET": "international-secret",
            "ALIBABA_CLOUD_SECURITY_TOKEN": "international-token",
            "ALIYUN_ACCESS_KEY_ID": "china-id",
            "ALIYUN_ACCESS_KEY_SECRET": "china-secret",
        }, clear=True):
            credentials = alibaba_credentials.read_international()
        self.assertEqual(credentials.access_key_id, "international-id")
        self.assertEqual(credentials.access_key_secret, "international-secret")
        self.assertEqual(credentials.security_token, "international-token")

    def test_international_hint_names_only_international_variables(self):
        hint = alibaba_credentials.missing_hint_international()
        self.assertIn("ALIBABA_CLOUD_ACCESS_KEY_ID", hint)
        self.assertIn("ALIBABA_CLOUD_SECURITY_TOKEN", hint)
        self.assertNotIn("ALIYUN_ACCESS_KEY_ID", hint)


if __name__ == "__main__":
    unittest.main()
