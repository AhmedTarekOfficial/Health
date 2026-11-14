from __future__ import annotations

import re
from typing import Dict


class VoiceAssistant:
    """Browser-oriented voice interface wrapper for spoken responses and transcripts."""

    # Spoken text stays short enough for the browser speech engine to finish,
    # while still being the real answer rather than a canned line.
    MAX_SPOKEN_CHARS = 480

    def __init__(self):
        self.voice_enabled = True

    @staticmethod
    def _plain_text(text: str) -> str:
        """Turn a markdown clinical reply into speech-friendly plain text."""
        cleaned = re.sub(r"```.*?```", "\n", text, flags=re.S)
        cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
        cleaned = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)
        cleaned = re.sub(r"https?://\S+", " ", cleaned)

        sentences = []
        for raw_line in cleaned.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Drop horizontal rules, tables separators and pure symbols.
            if not re.search(r"[A-Za-z0-9]", line):
                continue
            line = re.sub(r"^#{1,6}\s*", "", line)          # headings
            line = re.sub(r"^[-*+]\s+", "", line)          # bullet markers
            line = re.sub(r"^\d+[.)]\s+", "", line)        # numbered markers
            line = re.sub(r"[*_~>|]+", "", line)
            line = re.sub(r"\s+", " ", line).strip()
            if not line:
                continue
            # Each markdown line becomes its own spoken sentence.
            if line[-1] not in ".!?:;":
                line += "."
            sentences.append(line)

        return " ".join(sentences)

    def _spoken_summary(self, text: str) -> str:
        """Speak the opening of the reply, cut on a sentence boundary."""
        cleaned = self._plain_text(text)
        if len(cleaned) <= self.MAX_SPOKEN_CHARS:
            return cleaned
        cut = cleaned.rfind(". ", 0, self.MAX_SPOKEN_CHARS)
        if cut < self.MAX_SPOKEN_CHARS // 3:
            cut = cleaned.rfind(" ", 0, self.MAX_SPOKEN_CHARS)
        if cut <= 0:
            cut = self.MAX_SPOKEN_CHARS
        return f"{cleaned[:cut].rstrip(' ,;:.')}."

    def build_reply(self, text: str) -> Dict[str, object]:
        cleaned = (text or "").strip()
        spoken = self._spoken_summary(cleaned)
        if not spoken:
            spoken = "I am ready to help with the current telemetry."

        return {
            "voice_enabled": self.voice_enabled,
            "text": spoken,
            "transcript": cleaned,
        }
