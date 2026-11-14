# Medical knowledge base

This is the legacy bundled corpus, used by standalone `MedicalRAG()` callers.
Live chat now uses only the approved NASA/CSA web pages described in
[TRUSTED_WEB_RAG.md](../TRUSTED_WEB_RAG.md); editing this folder does not change
live chat references.

To add references, drop a new JSON file here (or extend an existing one):

```json
{
  "entries": [
    {
      "id": "unique-id",
      "title": "Short reference title",
      "source": "Publisher or guideline name",
      "url": "https://optional-link",
      "keywords": ["english terms", "مصطلحات عربية"],
      "text": "Two to four factual sentences the assistant may rely on."
    }
  ]
}
```

`title` and `text` are required. Keywords are indexed but never shown to the model, which is how
Arabic questions match English reference text in standalone searches. Rebuild the index after editing.
