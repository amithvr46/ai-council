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
                        try:
                            out.append(chr(int(self._unicode, 16)))
                        except ValueError:
                            pass
                        self._unicode = None
                elif self._escape:
                    self._escape = False
                    if ch == "u":
                        self._unicode = ""
                    else:
                        out.append(
                            {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}.get(
                                ch, ch
                            )
                        )
                elif ch == "\\":
                    self._escape = True
                elif ch == '"':
                    self._state = "done"
                else:
                    out.append(ch)
        return "".join(out)


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
