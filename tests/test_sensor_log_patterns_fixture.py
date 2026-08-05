import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "dev_serial_with_sensor_values.log"


def test_sensor_values_fixture_contains_real_json_snapshots():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()

    json_lines = [line for line in lines if line.startswith("{")]

    assert len(json_lines) == 3

    first_snapshot = json.loads(json_lines[0])
    assert first_snapshot["BME280"]["temperature"]["value"] == 25.51055
    assert first_snapshot["BME280"]["temperature"]["units"] == "°C"
    assert first_snapshot["ICS43434"]["noiseAvg"]["units"] == "dB"
    assert first_snapshot["SDS"]["P1"]["intl_name"] == "PM10"
    assert first_snapshot["SDS"]["P2"]["intl_name"] == "PM2.5"
