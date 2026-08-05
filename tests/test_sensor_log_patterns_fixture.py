import json
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "sensor_json_snapshots.log"


def test_sensor_values_fixture_contains_real_json_snapshots():
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 3

    first_snapshot = json.loads(lines[0])
    assert first_snapshot["BME280"]["temperature"]["value"] == 25.51055
    assert first_snapshot["BME280"]["temperature"]["units"] == "°C"
    assert first_snapshot["ICS43434"]["noiseAvg"]["units"] == "dB"
    assert first_snapshot["SDS"]["P1"]["intl_name"] == "PM10"
    assert first_snapshot["SDS"]["P2"]["intl_name"] == "PM2.5"
