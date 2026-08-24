from __future__ import annotations

from fractal.donor_signals import SessionEvent, extract_session_signals


def test_explicit_status_outranks_fallback_error_text() -> None:
    events = [
        SessionEvent(
            "tool-success",
            "session-a",
            1,
            "tool",
            content="Documentation says a timeout may occur",
            tool_name="docs",
            explicit_error=False,
        ),
        SessionEvent(
            "tool-error",
            "session-a",
            2,
            "tool",
            content="opaque provider response",
            tool_name="provider",
            explicit_error=True,
        ),
    ]
    signals = extract_session_signals(events)
    assert [item.event_ids for item in signals] == [("tool-error",)]
    assert signals[0].category == "explicit-error"
    assert signals[0].confidence == "high"


def test_fallback_signal_is_evidence_only_and_privacy_bounded() -> None:
    content = "Traceback: connection refused while calling the provider"
    signals = extract_session_signals(
        [SessionEvent("event-a", "session-a", 1, "tool", content, "provider")]
    )
    assert len(signals) == 1
    signal = signals[0]
    assert signal.signal_type == "tool-failure"
    assert signal.route == "find-problems"
    assert signal.automatic_change is False
    assert signal.content_sha256 is not None
    assert content not in str(signal.to_dict())


def test_retry_detection_stays_inside_one_session() -> None:
    events = [
        SessionEvent("a-1", "a", 1, "assistant", tool_calls=("web",)),
        SessionEvent("a-2", "a", 2, "assistant", tool_calls=("web",)),
        SessionEvent("b-1", "b", 3, "assistant", tool_calls=("web",)),
        SessionEvent("a-3", "a", 4, "assistant", tool_calls=("web",)),
    ]
    retries = [
        item for item in extract_session_signals(events) if item.signal_type == "rapid-retry"
    ]
    assert len(retries) == 1
    assert retries[0].session_id == "a"
    assert retries[0].event_ids == ("a-1", "a-2", "a-3")


def test_user_correction_is_possible_signal_not_a_causal_claim() -> None:
    content = "No, you misunderstood what I meant."
    signals = extract_session_signals([SessionEvent("user-a", "session-a", 1, "user", content)])
    assert len(signals) == 1
    assert signals[0].signal_type == "possible-user-correction"
    assert signals[0].confidence == "medium"
    assert signals[0].automatic_change is False


def test_duplicate_event_ids_are_rejected() -> None:
    events = [
        SessionEvent("same", "session-a", 1, "assistant"),
        SessionEvent("same", "session-a", 2, "assistant"),
    ]
    try:
        extract_session_signals(events)
    except ValueError as error:
        assert "unique ids" in str(error)
    else:
        raise AssertionError("duplicate event ids must be rejected")
