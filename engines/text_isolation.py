"""
============================================================
CMI — Text Isolation Engine

The cardinal rule of the CMI pipeline is:
    *** LLMs consume PLAINTEXT ONLY. ***

This module is the single chokepoint through which every piece
of external media must pass before it reaches the LLM router.
It converts HTML, images, audio, and video into clean Unicode
strings — nothing else exits this module.

Supported conversions (production-ready):
    • HTML  → plaintext   (BeautifulSoup strip)

Stubbed conversions (wire up real backends later):
    • Image → plaintext   (OCR via Tesseract / Google Vision)
    • Audio → plaintext   (Speech-to-text via Whisper / GCP)
    • Video → plaintext   (Keyframe extraction + OCR + STT)
============================================================
"""

from __future__ import annotations

import re
import logging
from enum import Enum
from typing import Optional
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone

from bs4 import BeautifulSoup

logger = logging.getLogger("cmi.engines.text_isolation")


# ── Public data contracts ──────────────────────────────────────

class MediaType(str, Enum):
    """Enum of every media format the isolation engine can accept."""
    HTML       = "html"
    PLAINTEXT  = "plaintext"
    IMAGE      = "image"
    AUDIO      = "audio"
    VIDEO      = "video"
    UNKNOWN    = "unknown"


@dataclass(frozen=True)
class IsolationResult:
    """
    Immutable output of every isolation pass.

    Attributes:
        text:        The extracted plaintext string.
        source_type: The MediaType that was fed in.
        char_count:  Length of the extracted text.
        warnings:    Any non-fatal issues encountered during extraction.
        timestamp:   UTC timestamp of when extraction occurred.
    """
    text: str
    source_type: MediaType
    char_count: int = 0
    warnings: list[str] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self):
        # frozen=True requires object.__setattr__ for computed fields
        object.__setattr__(self, "char_count", len(self.text))


# ── Core Engine ────────────────────────────────────────────────

class TextIsolationEngine:
    """
    Stateless media-to-plaintext converter.

    Usage:
        engine = TextIsolationEngine()
        result = engine.isolate(raw_html, MediaType.HTML)
        assert isinstance(result.text, str)
    """

    # Characters that should never survive into LLM context
    _CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

    # Collapse runs of whitespace into single spaces
    _WHITESPACE_RE = re.compile(r"[ \t]+")

    # ── Public API ─────────────────────────────────────────────

    def isolate(
        self,
        raw_input: str | bytes,
        media_type: MediaType,
        *,
        source_label: str = "",
    ) -> IsolationResult:
        """
        Master dispatcher — routes the input to the correct
        converter based on media_type and returns an IsolationResult.

        Args:
            raw_input:    The raw content (HTML string, file path, bytes).
            media_type:   Declared type of the incoming payload.
            source_label: Optional human-readable label for logging.

        Returns:
            IsolationResult with the cleaned plaintext.
        """
        label = source_label or media_type.value
        logger.info("Isolating content [%s] as %s", label, media_type.value)

        dispatch = {
            MediaType.HTML:      self._strip_html,
            MediaType.PLAINTEXT: self._passthrough_clean,
            MediaType.IMAGE:     self._stub_ocr,
            MediaType.AUDIO:     self._stub_speech_to_text,
            MediaType.VIDEO:     self._stub_video_to_text,
        }

        handler = dispatch.get(media_type, self._handle_unknown)
        return handler(raw_input)

    # ── HTML → Plaintext (production) ──────────────────────────

    def _strip_html(self, html: str | bytes) -> IsolationResult:
        """
        Strips ALL HTML/XML tags, decodes entities, and returns
        only the visible text content.

        Uses BeautifulSoup with the 'lxml' parser for speed.
        Script and style blocks are removed entirely before
        text extraction.
        """
        warnings: list[str] = []

        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")
            warnings.append("Input was bytes; decoded as UTF-8.")

        soup = BeautifulSoup(html, "lxml")

        # Remove script, style, and other non-visible elements
        for tag_name in ("script", "style", "noscript", "iframe", "svg"):
            for element in soup.find_all(tag_name):
                element.decompose()

        # Add newline markers after block-level elements so paragraph breaks are preserved
        for block_tag in soup.find_all(["p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"]):
            block_tag.append(" \n ")

        # Extract visible text with space separator so inline elements (b, i, a, span) remain contiguous
        raw_text = soup.get_text(separator=" ", strip=True)

        # Final sanitization pass
        clean = self._sanitize(raw_text)

        logger.debug(
            "HTML isolation complete: %d chars → %d chars",
            len(html), len(clean),
        )
        return IsolationResult(
            text=clean,
            source_type=MediaType.HTML,
            warnings=warnings,
        )

    # ── Plaintext passthrough (still sanitized) ────────────────

    def _passthrough_clean(self, text: str | bytes) -> IsolationResult:
        """
        Accepts already-plain text but still runs it through
        the sanitizer to strip control characters and normalize
        whitespace. Guarantees uniform output format.
        """
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        clean = self._sanitize(text)
        return IsolationResult(text=clean, source_type=MediaType.PLAINTEXT)

    # ── Image → Plaintext (STUB) ──────────────────────────────

    def _stub_ocr(self, image_input: str | bytes) -> IsolationResult:
        """
        STUB: Optical Character Recognition.

        In production, this will:
          1. Accept a file path or raw image bytes.
          2. Run Tesseract OCR (or Google Cloud Vision).
          3. Return the extracted text.

        Currently returns a mock transcript so downstream code
        can be developed and tested against the expected interface.
        """
        logger.warning("OCR stub invoked — returning mock transcript.")

        # Determine a label for the mock
        if isinstance(image_input, (str, Path)):
            label = str(image_input)
        else:
            label = f"<{len(image_input)} bytes>"

        mock_text = (
            f"[OCR STUB] Extracted text from image: {label}\n"
            f"Detected content: Road sign indicating lane closure "
            f"ahead. Speed limit reduced to 40 km/h. "
            f"Construction zone between KM 12.4 and KM 14.1."
        )
        return IsolationResult(
            text=mock_text,
            source_type=MediaType.IMAGE,
            warnings=["OCR is stubbed — using mock transcript."],
        )

    # ── Audio → Plaintext (STUB) ──────────────────────────────

    def _stub_speech_to_text(self, audio_input: str | bytes) -> IsolationResult:
        """
        STUB: Speech-to-Text transcription.

        In production, this will:
          1. Accept a file path or audio bytes (WAV, MP3, OGG).
          2. Run Whisper / Google STT / Azure STT.
          3. Return the transcription as plaintext.

        Currently returns a mock transcript.
        """
        logger.warning("STT stub invoked — returning mock transcript.")

        mock_text = (
            "[STT STUB] Audio transcription placeholder.\n"
            "Announcer: Due to waterlogging on Velachery Main Road, "
            "traffic has been diverted via Taramani Link Road. "
            "Expect delays of approximately 20 minutes during peak hours."
        )
        return IsolationResult(
            text=mock_text,
            source_type=MediaType.AUDIO,
            warnings=["Speech-to-text is stubbed — using mock transcript."],
        )

    # ── Video → Plaintext (STUB) ──────────────────────────────

    def _stub_video_to_text(self, video_input: str | bytes) -> IsolationResult:
        """
        STUB: Video-to-Text extraction.

        In production, this will:
          1. Extract keyframes at configurable intervals.
          2. Run OCR on each keyframe.
          3. Extract and transcribe the audio track.
          4. Merge all text streams chronologically.

        Currently returns a mock composite transcript.
        """
        logger.warning("Video-to-text stub invoked — returning mock transcript.")

        mock_text = (
            "[VIDEO STUB] Composite video transcript placeholder.\n"
            "00:00 — Aerial view of Mount Road showing heavy congestion.\n"
            "00:15 — Banner overlay reads: 'Metro construction Phase 2 — "
            "Expect single-lane traffic until March 2027.'\n"
            "00:30 — Reporter states: 'Chennai Metro Rail has announced "
            "temporary road closures along Anna Salai between Gemini "
            "Flyover and LIC Building for tunnel boring operations.'"
        )
        return IsolationResult(
            text=mock_text,
            source_type=MediaType.VIDEO,
            warnings=["Video-to-text is stubbed — using mock transcript."],
        )

    # ── Unknown media handler ─────────────────────────────────

    def _handle_unknown(self, raw_input: str | bytes) -> IsolationResult:
        """Handles unrecognized media types gracefully."""
        logger.error("Unknown media type received — cannot isolate.")
        return IsolationResult(
            text="",
            source_type=MediaType.UNKNOWN,
            warnings=["Unknown media type — no text extracted."],
        )

    # ── Internal sanitizer ────────────────────────────────────

    def _sanitize(self, text: str) -> str:
        """
        Final-pass text sanitizer applied to ALL outputs.

        1. Strips control characters (NUL, BEL, etc.)
        2. Normalizes horizontal whitespace (tabs → spaces, collapses runs)
        3. Strips leading/trailing whitespace per line
        4. Collapses multiple blank lines into a single blank line
        """
        # Step 1: Remove control characters
        text = self._CONTROL_CHAR_RE.sub("", text)

        # Step 2: Normalize whitespace within each line
        lines = text.splitlines()
        cleaned_lines = [
            self._WHITESPACE_RE.sub(" ", line).strip()
            for line in lines
        ]

        # Step 3: Collapse multiple blank lines
        result_lines: list[str] = []
        prev_blank = False
        for line in cleaned_lines:
            if not line:
                if not prev_blank:
                    result_lines.append("")
                prev_blank = True
            else:
                result_lines.append(line)
                prev_blank = False

        sanitized = "\n".join(result_lines).strip()
        # Clean up any orphaned spaces before standard punctuation marks
        return re.sub(r"\s+([.,;:!?])", r"\1", sanitized)
