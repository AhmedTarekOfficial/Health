import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "ASTRA" / "website"
if str(SITE) not in sys.path:
    sys.path.insert(0, str(SITE))

try:
    import sklearn  # noqa: F401

    HAVE_SKLEARN = True
except ImportError:  # the app itself requires scikit-learn, so skip outside its env
    HAVE_SKLEARN = False


@unittest.skipUnless(HAVE_SKLEARN, "scikit-learn is required for retrieval")
class MedicalRAGTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from medical_rag import MedicalRAG

        cls.rag = MedicalRAG()

    def test_knowledge_base_is_indexed(self):
        self.assertTrue(self.rag.ready)
        self.assertGreaterEqual(len(self.rag._entries), 20)  # noqa: SLF001

    def test_english_query_retrieves_relevant_reference(self):
        ids = [entry["id"] for entry in self.rag.search("risks of space radiation")]
        self.assertIn("space-radiation", ids)

    def test_arabic_query_matches_english_reference_via_keywords(self):
        ids = [entry["id"] for entry in self.rag.search("اعراض الحساسية الشديدة")]
        self.assertIn("anaphylaxis", ids)

    def test_off_topic_message_injects_no_reference(self):
        self.assertEqual(self.rag.search("hello, how are you?"), [])

    def test_context_block_lists_sources(self):
        context = self.rag.build_context("why do astronauts lose bone density")
        self.assertIn("[Medical Reference Context]", context)
        self.assertIn("Bone Mineral Density Loss", context)


if __name__ == "__main__":
    unittest.main()
