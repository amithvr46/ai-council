"""Incremental extraction of one string field from streaming JSON.

Final-answer stages use structured output, so their token stream is raw
JSON. This scanner watches the stream for `"<field>": "` and then emits the
string's decoded characters as they arrive — which lets the UI render the
final answer live while the model is still writing the rest of the object.
"""


class FieldStreamExtractor:
    """Feed raw JSON text chunks; get decoded increments of one field back."""

    def __init__(self, field: str = "final_answer"):
        self._needle = f'"{field}"'
        self._buffer = ""  # pre-match: sliding window to find the needle
        self._state = "seeking"  # seeking -> colon -> in_string -> done
        self._escape = False
        self._unicode: str | None = None  # collects \uXXXX hex digits
        # JSON encodes non-BMP characters (emoji) as a UTF-16 surrogate pair
        # of two \uXXXX escapes; a high surrogate waits here for its low half.
        self._high_surrogate: int | None = None

    def feed(self, chunk: str) -> str:
        out: list[str] = []
        for ch in chunk:
            if self._state == "done":
                break
            if self._state == "seeking":
                self._buffer += ch
                if self._buffer.endswith(self._needle):
                    self._state = "colon"
                elif len(self._buffer) > 4 * len(self._needle):
                    self._buffer = self._buffer[-len(self._needle) :]
            elif self._state == "colon":
                if ch == '"':
                    self._state = "in_string"
                elif ch not in (":", " ", "\t", "\n", "\r"):
                    # not a string value — bail back to seeking
                    self._state = "seeking"
                    self._buffer = ""
            elif self._state == "in_string":
                if self._unicode is not None:
                    self._unicode += ch
                    if len(self._unicode) == 4:
                        self._emit_code_point(self._unicode, out)
                        self._unicode = None
                elif self._escape:
                    self._escape = False
                    if ch == "u":
                        self._unicode = ""
                    else:
                        self._flush_surrogate(out)
                        out.append(
                            {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(
                                ch, ch
                            )
                        )
                elif ch == "\\":
                    # A pending high surrogate may still be completed by the
                    # escape that follows — don't flush it yet.
                    self._escape = True
                elif ch == '"':
                    self._flush_surrogate(out)
                    self._state = "done"
                else:
                    self._flush_surrogate(out)
                    out.append(ch)
        return "".join(out)

    def _emit_code_point(self, hex4: str, out: list[str]) -> None:
        """Decode one \\uXXXX escape, combining UTF-16 surrogate pairs so
        non-BMP characters (emoji) emit as a single code point."""
        try:
            code = int(hex4, 16)
        except ValueError:
            self._flush_surrogate(out)
            return

        if 0xD800 <= code <= 0xDBFF:  # high surrogate: wait for its partner
            self._flush_surrogate(out)  # two highs in a row: first is lone
            self._high_surrogate = code
            return

        if 0xDC00 <= code <= 0xDFFF:  # low surrogate
            if self._high_surrogate is not None:
                combined = 0x10000 + (self._high_surrogate - 0xD800) * 0x400 + (code - 0xDC00)
                self._high_surrogate = None
                out.append(chr(combined))
            else:
                out.append("�")  # lone low surrogate
            return

        self._flush_surrogate(out)
        out.append(chr(code))

    def _flush_surrogate(self, out: list[str]) -> None:
        """A high surrogate never completed — emit the replacement character
        rather than a bare surrogate, which cannot be UTF-8 encoded for SSE."""
        if self._high_surrogate is not None:
            self._high_surrogate = None
            out.append("�")


class DeltaThrottle:
    """Batch tiny increments into fewer SSE events (flush on ~word boundaries)."""

    def __init__(self, emit, min_chars: int = 16):
        self._emit = emit
        self._min = min_chars
        self._pending = ""

    def push(self, text: str) -> None:
        if not text:
            return
        self._pending += text
        if len(self._pending) >= self._min:
            self._emit(self._pending)
            self._pending = ""

    def flush(self) -> None:
        if self._pending:
            self._emit(self._pending)
            self._pending = ""
