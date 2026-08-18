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
