"""Unit tests for gateway.tracing -- the W3C traceparent parse/generate
helpers backing end-to-end trace correlation (docs/05 §6.3, docs/06 §6.3).
"""
from __future__ import annotations

from gateway import tracing

VALID = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
VALID_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"


class TestParse:
    def test_valid_header_returns_trace_id(self):
        assert tracing.parse(VALID) == VALID_TRACE_ID

    def test_none_returns_none(self):
        assert tracing.parse(None) is None

    def test_empty_string_returns_none(self):
        assert tracing.parse("") is None

    def test_malformed_header_returns_none(self):
        assert tracing.parse("not-a-traceparent") is None

    def test_wrong_segment_count_returns_none(self):
        assert tracing.parse("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7") is None

    def test_wrong_length_ids_return_none(self):
        assert tracing.parse("00-tooshort-00f067aa0ba902b7-01") is None

    def test_uppercase_hex_returns_none(self):
        # The spec requires lowercase hex -- a case-mismatched header is
        # exactly the kind of "close but not conforming" input that must
        # degrade to "start a new trace," not raise.
        assert tracing.parse("00-4BF92F3577B34DA6A3CE929D0E0E4736-00f067aa0ba902b7-01") is None

    def test_all_zero_trace_id_returns_none(self):
        # The spec's own reserved "invalid" trace-id.
        assert tracing.parse("00-00000000000000000000000000000000-00f067aa0ba902b7-01") is None

    def test_surrounding_whitespace_is_tolerated(self):
        assert tracing.parse(f"  {VALID}  ") == VALID_TRACE_ID


class TestTraceIdFor:
    def test_valid_inbound_header_is_reused(self):
        assert tracing.trace_id_for(VALID) == VALID_TRACE_ID

    def test_missing_header_mints_a_fresh_one(self):
        trace_id = tracing.trace_id_for(None)
        assert len(trace_id) == 32
        int(trace_id, 16)  # must be valid hex

    def test_malformed_header_mints_a_fresh_one_rather_than_propagating_garbage(self):
        trace_id = tracing.trace_id_for("garbage")
        assert trace_id != "garbage"
        assert len(trace_id) == 32

    def test_each_fresh_trace_id_is_unique(self):
        assert tracing.trace_id_for(None) != tracing.trace_id_for(None)


class TestOutboundHeader:
    def test_shape(self):
        header = tracing.outbound_header(VALID_TRACE_ID)
        assert tracing.parse(header) == VALID_TRACE_ID

    def test_preserves_trace_id_but_mints_a_new_span_id(self):
        # The whole point of propagation: same trace-id downstream, but a
        # FRESH span-id -- the gateway's own hop is a new child span, not a
        # re-announcement of whichever span sent the inbound request.
        header = tracing.outbound_header(VALID_TRACE_ID)
        inbound_span_id = VALID.split("-")[2]
        outbound_span_id = header.split("-")[2]
        assert outbound_span_id != inbound_span_id

    def test_sampled_flag_default_true(self):
        header = tracing.outbound_header(VALID_TRACE_ID)
        assert header.endswith("-01")

    def test_sampled_flag_false(self):
        header = tracing.outbound_header(VALID_TRACE_ID, sampled=False)
        assert header.endswith("-00")

    def test_each_call_mints_a_different_span_id(self):
        h1 = tracing.outbound_header(VALID_TRACE_ID)
        h2 = tracing.outbound_header(VALID_TRACE_ID)
        assert h1 != h2


def test_new_trace_id_and_new_span_id_lengths():
    assert len(tracing.new_trace_id()) == 32
    assert len(tracing.new_span_id()) == 16
