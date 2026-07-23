import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from relay_checkin import CheckinError, SiteConfig, check_in, load_sites, main


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status = status
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.body


def make_site():
    return SiteConfig.from_mapping(
        {
            "id": "example",
            "name": "Example Relay",
            "homepage": "https://relay.example/",
            "base_url": "https://relay.example",
            "checkin_path": "/api/user/checkin",
            "access_token_env": "EXAMPLE_ACCESS_TOKEN",
            "user_id_env": "EXAMPLE_USER_ID",
            "already_checked_in_messages": ["already checked in", "\u4eca\u65e5\u5df2\u7b7e\u5230"],
        }
    )


class CheckinTests(unittest.TestCase):
    def setUp(self):
        self.site = make_site()
        self.environ = {
            "EXAMPLE_ACCESS_TOKEN": "test-token",
            "EXAMPLE_USER_ID": "123",
        }

    def test_successful_checkin(self):
        captured = {}

        def opener(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse(
                {
                    "success": True,
                    "message": "ok",
                    "data": {"quota_awarded": 42},
                }
            )

        result = check_in(self.site, self.environ, timeout=12, opener=opener)
        self.assertEqual(result.quota_awarded, 42)
        self.assertEqual(captured["timeout"], 12)
        self.assertEqual(captured["request"].get_header("Authorization"), "test-token")
        self.assertEqual(captured["request"].get_header("New-api-user"), "123")
        self.assertEqual(captured["request"].data, b"{}")

    def test_already_checked_in_is_idempotent_success(self):
        result = check_in(
            self.site,
            self.environ,
            opener=lambda request, timeout: FakeResponse(
                {"success": False, "message": "\u4eca\u65e5\u5df2\u7b7e\u5230"}
            ),
        )
        self.assertTrue(result.already_checked_in)

    def test_rejected_checkin_raises(self):
        with self.assertRaisesRegex(CheckinError, "access token invalid"):
            check_in(
                self.site,
                self.environ,
                opener=lambda request, timeout: FakeResponse(
                    {"success": False, "message": "access token invalid"}
                ),
            )

    def test_missing_credentials_do_not_send_a_request(self):
        with self.assertRaisesRegex(CheckinError, "EXAMPLE_ACCESS_TOKEN"):
            check_in(self.site, {}, opener=lambda request, timeout: self.fail("called"))

    def test_config_rejects_non_https_urls(self):
        config = {
            "sites": [
                {
                    "id": "bad",
                    "name": "Bad",
                    "homepage": "http://relay.example/",
                    "base_url": "http://relay.example",
                    "checkin_path": "/checkin",
                    "access_token_env": "TOKEN",
                    "user_id_env": "USER_ID",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sites.json")
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(CheckinError, "HTTPS"):
                load_sites(path)

    def test_dry_run_does_not_print_secrets(self):
        with patch.dict(os.environ, self.environ, clear=True), patch(
            "relay_checkin.load_sites", return_value=[self.site]
        ), patch("builtins.print") as output:
            exit_code = main(["--dry-run"])
        self.assertEqual(exit_code, 0)
        rendered = " ".join(str(call) for call in output.call_args_list)
        self.assertNotIn("test-token", rendered)


if __name__ == "__main__":
    unittest.main()
