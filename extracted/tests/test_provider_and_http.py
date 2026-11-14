import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBSITE_DIR = ROOT / "ASTRA" / "website"
if str(WEBSITE_DIR) not in sys.path:
    sys.path.insert(0, str(WEBSITE_DIR))


class ProviderConfigTests(unittest.TestCase):
    def test_provider_defaults_to_groq(self):
        os.environ["LLM_PROVIDER"] = "groq"
        os.environ.pop("GROQ_API_KEY", None)
        os.environ.pop("GEMINI_API_KEY", None)

        from provider_config import get_provider_settings

        config = get_provider_settings()
        self.assertEqual(config["provider"], "groq")
        self.assertEqual(config["route"], "groq")
        self.assertFalse(config["api_key_present"])
        self.assertIn(config["fallback_provider"], {None, "groq"})


class WebsiteEndpointTests(unittest.TestCase):
    def test_app_status_route_returns_ok(self):
        from app import app

        client = app.test_client()
        response = client.get("/api/status")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "operational")


if __name__ == "__main__":
    unittest.main()
