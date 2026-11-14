# Approved web references and readable chat

The Flask app now combines its existing server-owned telemetry snapshot with
passages fetched from the four NASA/CSA pages in `trusted_sources.json`.
It is bounded web retrieval: it searches the text of those pages, not the whole
internet or the documents linked from them. No new API key or package is needed.

## How retrieval works

1. On the first chat request, fetch missing or expired approved pages in parallel.
2. Extract visible article text, split it into passages, and index with local
   TF-IDF word/character search. Common Arabic topic terms expand into English
   search terms. This is lightweight bilingual matching, not a multilingual
   embedding model; some paraphrases may not retrieve a match.
3. Send up to four matching passages to the existing LLM along with telemetry.
   Instructions separate source data from instructions and require citation IDs
   such as `[S4]`, disclosure of missing evidence, and simple readable language.
4. Return server-generated reference links, fetch timestamps and retrieval status
   to the chat UI. A retrieved reference is a candidate source, not proof that
   every claim in the generated answer is supported. Citations are prompt-guided;
   the implementation does not independently verify medical claims.

Only the exact URLs in `trusted_sources.json` may be fetched, including redirect
destinations. User questions, telemetry and history are **not** sent to NASA/CSA;
search runs locally. They still go to the existing chat provider as before.
The app does not execute retrieved content or follow links in articles.

## Cache and offline operation

- Cache: `.cache/trusted_references.json` (ignored by Git).
- Refresh interval: 24 hours. Failed refresh retry: after 5 minutes.
- Timeout: 8 seconds per HTTP operation; response size limit: 2 MB per page.
- App startup itself does not fetch references. The first chat can take longer
  while pages are fetched; later chats normally use the cached index.
- If a refresh fails, retain the previous text and show an older-copy notice.
- With no cached text and no connection, chat remains available and explicitly
  receives an unavailable-reference status. There is no fallback to unapproved
  sites or the old bundled medical summaries.
- The date shown is when OrbitNexus fetched the page, not its publication date.

To change the approved pages, edit `trusted_sources.json` and restart Flask.
Use stable unique source IDs. The original `medical_knowledge/*.json` files remain
available to standalone `MedicalRAG()` callers but are not used by live chat.

## Limits of these references

The selected pages describe spaceflight hazards, health risks and research.
They are not a comprehensive astronaut treatment manual. A relevant research
passage does not necessarily support treatment instructions. The prompt requires
the model to disclose missing evidence rather than invent diagnoses, medication
doses or procedures, and to distinguish simulated readings from real readings.
This retrieval feature has not been clinically validated.

## Chat presentation

`src/modules/checkin/message-renderer.js` renders a small Markdown subset with
safe DOM nodes. Each paragraph/list chooses its own reading direction; Latin
terms, values and units are isolated inside Arabic text. Raw HTML is displayed
as text. `reference-sources.js` renders server reference metadata separately;
styles are in `message-content.css`.

## Run and verify

Run the integrated Flask app using the project's existing environment and Groq
configuration. From `OrbitNexus/extracted`:

```bash
.venv/bin/python ASTRA/website/app.py
```

Open `http://localhost:5000` and refresh the browser after restarting Flask.
The original static preview on port 5173 is a separate website.

Automated retrieval tests use synthetic pages and a mocked LLM; they make no
paid LLM calls and do not load the ML model files:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests -p 'test_trusted_web_rag.py' -v
```

For the browser layout fixture, serve `OrbitNexus/extracted` with a local HTTP
server and open `/tests/chat_message_preview.html`. It checks mixed Arabic and
English text, safe rendering, source status, and narrow-width overflow without
calling the backend.
