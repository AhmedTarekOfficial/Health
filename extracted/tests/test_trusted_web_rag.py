"""Approved-page retrieval checks: no live network, LLM calls, or model loads."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request

SITE = Path(__file__).resolve().parents[1] / "ASTRA" / "website"
sys.path.insert(0, str(SITE))
from trusted_web_rag import (  # noqa: E402
    ALLOWED_URLS, SOURCES, TTL_SECONDS, RETRY_SECONDS,
    ApprovedRedirects, TrustedWebRAG, build_search_query, extract_article, fetch_source,
)

# Synthetic search fixtures, not medical guidance.
TEXTS = {
    "S1": "Spaceflight hazards include isolation, confinement and distance from Earth. " * 5,
    "S2": "Bone density and muscle loss are topics of research in microgravity. " * 5,
    "S3": "Sleep circadian rhythms and astronaut fatigue are studied during missions. " * 5,
    "S4": "CANS neutron radiation detector measures radiation exposure in space. " * 5,
}


class TrustedWebRAGTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cache = Path(self.directory.name) / "references.json"
        self.now = 1_800_000_000
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        source = next(source for source in SOURCES if source["url"] == url)
        return TEXTS[source["id"]]

    def rag(self, fetcher=None):
        return TrustedWebRAG(cache_path=self.cache, fetcher=fetcher or self.fetch, clock=lambda: self.now)

    def test_article_excludes_navigation_scripts_and_hidden_text(self):
        html = ('<nav>outside navigation</nav><main><h1>Research</h1>'
                '<nav>inside navigation</nav><script>danger()</script>'
                '<p hidden>hidden words</p><p aria-hidden="true">invisible</p>'
                '<p>' + TEXTS["S4"] + '</p><footer>footer words</footer></main>')
        result = extract_article(html)
        self.assertIn(TEXTS["S4"].strip(), result)
        for unwanted in ("navigation", "danger", "hidden", "invisible", "footer"):
            self.assertNotIn(unwanted, result)

    def test_unapproved_url_and_redirect_are_rejected_before_fetch(self):
        with patch("trusted_web_rag.build_opener") as opener:
            with self.assertRaises(ValueError):
                fetch_source("https://www.nasa.gov/not-approved/")
            opener.assert_not_called()
        with self.assertRaises(ValueError):
            ApprovedRedirects().redirect_request(
                Request(SOURCES[0]["url"]), None, 302, "Found", {}, "http://127.0.0.1/private")

    def test_fetches_only_four_pages_without_user_data(self):
        rag = self.rag()
        self.assertEqual(self.calls, [])  # Import/startup is offline.
        result = rag.retrieve("CANS neutron radiation my-private-symptoms")
        self.assertCountEqual(self.calls, ALLOWED_URLS)
        self.assertEqual(result["status"]["state"], "matched")
        self.assertIn("S4", [source["id"] for source in result["sources"]])
        self.assertNotIn("my-private-symptoms", result["context"])

    def test_arabic_query_finds_actual_english_page(self):
        result = self.rag().retrieve("مخاطر الإشعاع والنيوترونات")
        self.assertIn("S4", [source["id"] for source in result["sources"]])
        self.assertIn("CANS neutron radiation detector", result["context"])

    def test_short_new_topic_does_not_inherit_previous_answer(self):
        history = [{"role": "user", "content": "radiation"},
                   {"role": "assistant", "content": "Generated radiation explanation"}]
        self.assertEqual(build_search_query("sleep", history), "sleep")
        self.assertEqual(build_search_query("ليه نومي مش منتظم؟", history), "ليه نومي مش منتظم؟")

    def test_explicit_followup_uses_user_topic_not_generated_claims(self):
        history = [{"role": "user", "content": "radiation"},
                   {"role": "assistant", "content": "unverified generated claim"}]
        for followup in ["is that dangerous?", "هل ده خطر؟"]:
            query = build_search_query(followup, history)
            self.assertIn("radiation", query)
            self.assertNotIn("unverified", query)

    def test_every_source_is_traceable_to_retrieved_passage(self):
        result = self.rag().retrieve("bone muscle loss")
        data = json.loads(result["context"].split("\n")[-1])
        self.assertEqual({p["citation_id"] for p in data["passages"]},
                         {s["id"] for s in result["sources"]})
        for passage in data["passages"]:
            self.assertIn(passage["url"], ALLOWED_URLS)
            self.assertIn(passage["passage"], TEXTS[passage["citation_id"]])
        self.assertTrue(all(s["fetched_at"].endswith("+00:00") for s in result["sources"]))

    def test_fresh_cache_is_reused_after_restart(self):
        self.rag().retrieve("radiation")
        self.calls.clear()
        result = self.rag().retrieve("radiation")
        self.assertEqual(self.calls, [])
        self.assertTrue(result["sources"])
        self.assertFalse(any(s["stale"] for s in result["sources"]))

    def test_expired_cache_is_identified_when_network_is_down(self):
        first = self.rag().retrieve("radiation")
        self.now += TTL_SECONDS + 1
        def offline(url):
            raise OSError("offline")
        result = self.rag(offline).retrieve("radiation")
        self.assertEqual(result["status"]["state"], "matched")
        self.assertTrue(all(s["stale"] for s in result["sources"]))
        self.assertEqual(result["sources"][0]["fetched_at"], first["sources"][0]["fetched_at"])
        self.assertCountEqual(result["status"]["stale_sources"], TEXTS)

    def test_no_cache_offline_does_not_invent_references_and_throttles_retry(self):
        def offline(url):
            self.calls.append(url)
            raise OSError("offline")
        rag = self.rag(offline)
        result = rag.retrieve("radiation")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["status"]["state"], "unavailable")
        rag.retrieve("radiation")
        self.assertEqual(len(self.calls), 4)
        self.now += RETRY_SECONDS + 1
        rag.retrieve("radiation")
        self.assertEqual(len(self.calls), 8)

    def test_no_match_does_not_load_bundled_medical_advice(self):
        result = self.rag().retrieve("zyxwqv")
        self.assertEqual(result["sources"], [])
        self.assertEqual(result["status"]["state"], "no_match")
        self.assertNotIn("anaphylaxis", result["context"])

    def test_tampered_cache_url_is_not_used(self):
        self.cache.write_text(json.dumps({"S4": {"url": "https://example.com/", "text": TEXTS["S4"],
                                                   "fetched_epoch": self.now}}))
        rag = self.rag()
        self.assertEqual(rag._documents, {})
        self.assertIn("S4", [s["id"] for s in rag.retrieve("radiation")["sources"]])


class TrustedRetrievalChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_telemetry_chat_integration import _install_mock_engine_before_app_import
        _install_mock_engine_before_app_import()
        import app
        cls.site = app

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.site.chat_history.clear()
        self.addCleanup(self.site.chat_history.clear)
        self.client = self.site.app.test_client()

    def test_chat_receives_passages_and_returns_same_source_metadata(self):
        rag = TrustedWebRAG(cache_path=Path(self.directory.name) / "cache.json",
                            fetcher=lambda url: TEXTS[next(s["id"] for s in SOURCES if s["url"] == url)])
        with patch.object(self.site, "medical_rag", rag), \
                patch.object(self.site, "invoke_groq_chat", return_value={"reply": "Research [S4]", "status": "ok"}) as llm, \
                patch.object(self.site.memory_manager, "add_event"):
            response = self.client.post("/api/chat", json={"message": "Explain CANS neutron radiation"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["knowledge_status"]["state"], "matched")
        self.assertIn("S4", [s["id"] for s in body["knowledge_sources"]])
        context = llm.call_args.args[0][0]["content"]
        self.assertIn("CANS neutron radiation detector", context)
        self.assertIn(SOURCES[3]["url"], context)
        self.assertIn("Telemetry", context)

    def test_chat_still_works_without_reference_network(self):
        def offline(url):
            raise OSError("offline")
        rag = TrustedWebRAG(cache_path=Path(self.directory.name) / "cache.json", fetcher=offline)
        with patch.object(self.site, "medical_rag", rag), \
                patch.object(self.site, "invoke_groq_chat", return_value={"reply": "References unavailable", "status": "ok"}), \
                patch.object(self.site.memory_manager, "add_event"):
            response = self.client.post("/api/chat", json={"message": "radiation"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["knowledge_status"]["state"], "unavailable")
        self.assertEqual(response.get_json()["knowledge_sources"], [])


if __name__ == "__main__":
    unittest.main()
