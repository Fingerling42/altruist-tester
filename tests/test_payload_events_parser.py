import pytest

from altruist_tester.parsers.payload_events import parse_payload_observation


def test_parse_payload_observation_metadata():
    observation = parse_payload_observation(
        "[123] [INFO] [PAYLOAD] channel=datalog encoding=cps encrypted=1 "
        "payload_len=324"
    )

    assert observation is not None
    assert observation.as_event_payload() == {
        "channel": "datalog",
        "encoding": "cps",
        "encrypted": True,
        "payload_len": 324,
        "raw_fields": {
            "channel": "datalog",
            "encoding": "cps",
            "encrypted": "1",
            "payload_len": "324",
        },
    }


def test_payload_observation_event_payload_does_not_store_sample():
    observation = parse_payload_observation(
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=49 "
        "sample=h:65.15,t:25.84"
    )

    assert observation is not None
    assert observation.as_event_payload()["raw_fields"] == {
        "channel": "datalog",
        "encoding": "plain",
        "encrypted": "0",
        "payload_len": "49",
    }


def test_payload_observation_ignores_unrelated_lines():
    assert parse_payload_observation("[DATALOG] success response_len=66") is None


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("[PAYLOAD]", "missing_channel"),
        ("[PAYLOAD]   ", "missing_channel"),
        ("[PAYLOAD] encoding=plain", "missing_channel"),
        ("[PAYLOAD] channel= encoding=plain", "missing_channel"),
        ("[1] [INFO] [PAYLOAD] channel=sensors-connectivit", "unknown_channel"),
        ("[PAYLOAD] channel=future", "unknown_channel"),
    ],
)
def test_payload_channel_errors_are_classified(text, reason):
    observation = parse_payload_observation(text)
    assert observation is not None
    assert observation.malformed_reason == reason


@pytest.mark.parametrize("channel", ["datalog", "sensors-connectivity", "custom-http"])
def test_known_payload_channels_remain_valid(channel):
    observation = parse_payload_observation(f"[PAYLOAD] channel={channel}")
    assert observation is not None
    assert observation.malformed_reason is None
