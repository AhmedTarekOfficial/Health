import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "ASTRA" / "website"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))


class MemoryAndVoiceTests(unittest.TestCase):
    def test_memory_manager_tracks_recent_health_events(self):
        from memory_manager import MemoryManager

        manager = MemoryManager()
        manager.add_event("heart_rate", "elevated", {"value": 120})
        manager.add_event("sleep", "recovery", {"value": 7.5})

        recent = manager.get_recent_context(limit=5)
        self.assertGreaterEqual(len(recent), 2)
        self.assertTrue(any("heart_rate" in event["type"] for event in recent))

    def test_voice_assistant_builds_response_payload(self):
        from voice_assistant import VoiceAssistant

        assistant = VoiceAssistant()
        payload = assistant.build_reply("Heart rate looks elevated.")
        self.assertIn("voice_enabled", payload)
        self.assertIn("text", payload)
        self.assertIn("heart rate", payload["text"].lower())


if __name__ == "__main__":
    unittest.main()
