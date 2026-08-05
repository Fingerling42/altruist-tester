from pathlib import Path

from altruist_tester.parsers.sensor_values import (
    parse_payload_metadata,
    parse_sensor_values,
)

FIXTURE = Path(__file__).parent / "fixtures" / "dev_serial_with_sensor_values.log"


def test_parse_sensor_json_snapshot_from_fixture():
    line = next(line for line in FIXTURE.read_text(encoding="utf-8").splitlines())

    samples = parse_sensor_values(line)

    assert [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ] == [
        ("BME280", "humidity", 65.98633, "%"),
        ("BME280", "temperature", 25.51055, "°C"),
        ("BME280", "pressure", 101069.1, "hPa"),
        ("ICS43434", "noiseMax", 84.0, "dB"),
        ("ICS43434", "noiseAvg", 81.0, "dB"),
        ("SDS", "P1", 16.33125, "ppm"),
        ("SDS", "P2", 7.33125, "ppm"),
    ]
    assert {sample.source for sample in samples} == {"serial"}


def test_parse_payload_sample_supports_all_firmware_aliases():
    line = (
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=64 "
        "sample_available=1 sample=gc:1.5,co2:612,co:0.1,o3:0.2,no2:0.3,"
        "fa:42,ea:55"
    )

    samples = parse_sensor_values(line)

    assert [(sample.metric, sample.value, sample.unit) for sample in samples] == [
        ("radiation", 1.5, "µR/h"),
        ("co2", 612.0, "ppm"),
        ("co", 0.1, "ppm"),
        ("o3", 0.2, "ppm"),
        ("no2", 0.3, "ppm"),
        ("fast_aqi", 42.0, None),
        ("epa_aqi", 55.0, None),
    ]


def test_parse_payload_metadata_after_uart_log_prefix():
    line = (
        "[123] [INFO] [PAYLOAD] channel=datalog encoding=plain encrypted=0 "
        "payload_len=49 sample_available=1 sample=h:65.15,t:25.84"
    )

    fields = parse_payload_metadata(line)

    assert fields == {
        "channel": "datalog",
        "encoding": "plain",
        "encrypted": "0",
        "payload_len": "49",
        "sample_available": "1",
        "sample": "h:65.15,t:25.84",
    }


def test_parse_plain_payload_sample_values():
    line = (
        "[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=49 "
        "sample_available=1 sample=h:65.15,t:25.84,p:99860.91"
    )

    samples = parse_sensor_values(line)

    assert [
        (sample.sensor, sample.metric, sample.value, sample.unit, sample.source)
        for sample in samples
    ] == [
        ("datalog", "humidity", 65.15, "%", "serial_payload_datalog"),
        ("datalog", "temperature", 25.84, "°C", "serial_payload_datalog"),
        ("datalog", "pressure", 99860.91, "Pa", "serial_payload_datalog"),
    ]


def test_parse_payload_ignores_encrypted_payload_without_sample():
    line = (
        "[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324 "
        "sample_available=0"
    )

    assert parse_sensor_values(line) == []


def test_parse_encrypted_debug_payload_uses_plain_sample_only():
    line = (
        "[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324 "
        "sample_available=1 sample=h:65.15,t:25.84,p1:eJxEncrypted,p2:7.37"
    )

    samples = parse_sensor_values(line)

    assert [(sample.metric, sample.value, sample.source) for sample in samples] == [
        ("humidity", 65.15, "serial_payload_datalog"),
        ("temperature", 25.84, "serial_payload_datalog"),
        ("P2", 7.37, "serial_payload_datalog"),
    ]


def test_parse_mixed_connectivity_payload_marks_source_channel():
    line = (
        "[PAYLOAD] channel=sensors-connectivity encoding=mixed encrypted=1 "
        "payload_len=280 sample_available=1 sample=h:65.15,t:25.84"
    )

    samples = parse_sensor_values(line)

    assert [(sample.metric, sample.source) for sample in samples] == [
        ("humidity", "serial_payload_sensors_connectivity"),
        ("temperature", "serial_payload_sensors_connectivity"),
    ]


def test_parse_sensor_json_snapshot_with_uart_text_around_it():
    line = (
        '[123] [INFO] prefix {"SCD4x":{"co2":{"value":612,'
        '"intl_name":"CO2","units":"ppm"}}}[124] [INFO] suffix'
    )

    samples = parse_sensor_values(line)

    parsed = [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ]
    assert parsed == [
        ("SCD4x", "co2", 612.0, "ppm"),
    ]


def test_parse_concatenated_sensor_json_snapshots():
    line = (
        '{"SCD4x":{"co2":{"value":612,"units":"ppm"}}}'
        '{"BME680":{"pressure":{"value":101325,"units":"Pa"}}}'
    )

    samples = parse_sensor_values(line)

    parsed = [
        (sample.sensor, sample.metric, sample.value, sample.unit) for sample in samples
    ]
    assert parsed == [
        ("SCD4x", "co2", 612.0, "ppm"),
        ("BME680", "pressure", 101325.0, "Pa"),
    ]


def test_parse_sensor_values_ignores_non_sensor_lines_and_bad_json():
    assert parse_sensor_values("=== [URBAN] METRICS ===") == []
    assert parse_sensor_values('{"service_data":{"signal_strength":-40}}') == []
    assert parse_sensor_values('{"BME280":{"temperature":{"value":"bad"}}}') == []
    assert parse_sensor_values('{"BME280":{"temperature":{"value":true}}}') == []
    assert parse_sensor_values("{not json") == []
    assert parse_sensor_values("Datalog data: : h:65.99,t:25.51,p1:16.33") == []
