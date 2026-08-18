"""Token streaming: field extraction from JSON streams + engine delta events."""

from council.engine.schemas import CombinedCheck, Synthesis
from council.engine.streaming import DeltaThrottle, FieldStreamExtractor
from tests.fakes import FakeProvider

AGREE = CombinedCheck(agreement="agree", disagreement_type="none", summary="same")
SYNTH = Synthesis(final_answer="The **streamed** answer.")


def _feed_all(extractor, text, chunk_size=3):
    out = ""
    for i in range(0, len(text), chunk_size):
        out += extractor.feed(text[i : i + chunk_size])
    return out


def test_extractor_field_first():
    raw = '{"final_answer": "Hello world", "notes": "x"}'
    assert _feed_all(FieldStreamExtractor(), raw) == "Hello world"


def test_extractor_field_last():
    raw = '{"decision": "choose_a", "rationale": "because", "final_answer": "Use A."}'
    assert _feed_all(FieldStreamExtractor(), raw) == "Use A."


def test_extractor_handles_escapes_and_unicode():
    raw = '{"final_answer": "line1\\nline2 \\"quoted\\" \\u00e9tape"}'
    assert _feed_all(FieldStreamExtractor(), raw) == 'line1\nline2 "quoted" étape'


def test_extractor_split_anywhere():
    raw = '{"a": 1, "final_answer": "chunk boundaries do not matter"}'
    for size in (1, 2, 5, 7, 100):
        assert _feed_all(FieldStreamExtractor(), raw, size) == "chunk boundaries do not matter"


def test_extractor_ignores_similar_keys():
    raw = '{"not_final_answer_x": "wrong", "final_answer": "right"}'
    # needle match is on the exact quoted key
    assert _feed_all(FieldStreamExtractor(), raw) == "right"


def test_extractor_stops_at_close_quote():
    raw = '{"final_answer": "done", "trailing": "should not leak"}'
    assert _feed_all(FieldStreamExtractor(), raw) == "done"


# --- surrogate pairs (GPT streaming review #1) -------------------------------


def test_extractor_combines_surrogate_pair_emoji():
    # 😀 is U+1F600, encoded in JSON as the pair 😀
    raw = '{"final_answer": "ship it \\ud83d\\ude00 now"}'
    out = _feed_all(FieldStreamExtractor(), raw)
    assert out == "ship it 😀 now"
    assert len(out) == len("ship it 😀 now")
    out.encode("utf-8")  # must be encodable for SSE


def test_extractor_surrogate_pair_split_across_every_boundary():
    raw = '{"final_answer": "a\\ud83d\\ude00b"}'
    for size in range(1, len(raw) + 1):
        assert _feed_all(FieldStreamExtractor(), raw, size) == "a😀b"


def test_extractor_multiple_non_bmp_characters():
    raw = '{"final_answer": "\\ud83d\\ude80 \\ud83c\\udf89 \\ud83d\\udc4d"}'
    out = _feed_all(FieldStreamExtractor(), raw, 2)
    assert out == "🚀 🎉 👍"
    out.encode("utf-8")


def test_extractor_lone_high_surrogate_does_not_break_encoding():
    raw = '{"final_answer": "bad \\ud83d end"}'
    out = _feed_all(FieldStreamExtractor(), raw)
    assert out == "bad � end"
    out.encode("utf-8")  # would raise if a bare surrogate leaked


def test_extractor_lone_low_surrogate_replaced():
    raw = '{"final_answer": "x\\ude00y"}'
    out = _feed_all(FieldStreamExtractor(), raw)
    assert out == "x�y"
    out.encode("utf-8")


def test_extractor_high_surrogate_at_end_of_string():
    raw = '{"final_answer": "trailing \\ud83d"}'
    out = _feed_all(FieldStreamExtractor(), raw)
    assert out == "trailing �"
    out.encode("utf-8")


def test_extractor_bmp_escape_still_works_after_surrogate_logic():
    raw = '{"final_answer": "caf\\u00e9 \\u263a"}'
    assert _feed_all(FieldStreamExtractor(), raw) == "café ☺"


def test_extractor_mixed_escapes_and_emoji():
    raw = '{"final_answer": "line\\n\\ud83d\\ude00\\t\\"q\\" \\\\ done"}'
    out = _feed_all(FieldStreamExtractor(), raw, 3)
    assert out == 'line\n😀\t"q" \\ done'
    out.encode("utf-8")


def test_throttle_batches_and_flushes():
    got = []
    t = DeltaThrottle(got.append, min_chars=10)
    t.push("abc")
    t.push("def")
    assert got == []  # below threshold
    t.push("ghijk")  # crosses threshold
    assert got == ["abcdefghijk"]
    t.push("x")
    t.flush()
    assert got == ["abcdefghijk", "x"]


async def test_council_synthesis_streams_final_answer(make_engine):
    events = []
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    engine._publish = lambda rid, e: events.append(e)

    result = await engine.run("q?", "council")

    deltas = [e for e in events if e["type"] == "delta"]
    synth_text = "".join(e["text"] for e in deltas if e["stage"] == "synthesis")
    assert synth_text == "The **streamed** answer."
    assert result["final_answer"] == "The **streamed** answer."
    # candidates in council mode do NOT stream (their text goes to the trace)
    assert not [e for e in deltas if e["stage"].startswith("candidate")]


async def test_quick_streams_plain_text(make_engine):
    events = []
    openai = FakeProvider("openai", ["Plain streamed answer"])
    anthropic = FakeProvider("anthropic", [])
    engine = make_engine(openai, anthropic, quick_mode_strategy="openai")
    engine._publish = lambda rid, e: events.append(e)

    result = await engine.run("q?", "quick")

    text = "".join(e["text"] for e in events if e["type"] == "delta")
    assert text == "Plain streamed answer"
    assert result["final_answer"] == "Plain streamed answer"


async def test_no_publisher_means_no_streaming(make_engine):
    openai = FakeProvider("openai", ["GPT answer", AGREE, SYNTH])
    anthropic = FakeProvider("anthropic", ["Claude answer"])
    engine = make_engine(openai, anthropic, check_provider="openai")
    engine._publish = None

    result = await engine.run("q?", "council")  # must not raise
    assert result["final_answer"] == "The **streamed** answer."
