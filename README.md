# Altruist Tester

Python-based post-assembly tester for Altruist devices. The current workflow is
focused on one device connected over USB-C serial to a Linux host, Raspberry Pi,
or development machine.

The tester captures raw firmware logs, writes structured run artifacts, parses
known sensor samples and dev metrics, and can check that required sensor metrics
appeared during the run.

## Quick Start

Install dependencies:

```bash
uv sync
```

List detected serial ports:

```bash
uv run altruist-tester ports
```

Run a short smoke test with the only detected serial port:

```bash
uv run altruist-tester run --auto --duration 10m
```

Run against an explicit port:

```bash
uv run altruist-tester run --port /dev/ttyACM0 --duration 10m
```

Run with required Urban sensors:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-sensor bme280 \
  --expect-sensor sds \
  --expect-sensor ics-43434
```

This checks the usual Urban sensor set:

- `bme280`: temperature, humidity, pressure;
- `sds`: PM10 and PM2.5;
- `ics-43434`: average and max noise.

The same Urban expectations and rule thresholds can be loaded from a TOML
profile:

```bash
uv run altruist-tester run --auto --duration 10m \
  --config configs/urban.example.toml
```

Run with required Insight sensors:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-sensor scd41 \
  --expect-sensor bme680
```

This checks the usual Insight sensor set:

- `scd41`: CO2, temperature, humidity;
- `bme680`: temperature, humidity, pressure.

Current firmware uses BME680 for temperature, humidity, and pressure only; the
gas heater is disabled.

Insight expectations and rule thresholds can also be loaded from a TOML profile:

```bash
uv run altruist-tester run --auto --duration 10m \
  --config configs/insight.example.toml
```

`--expect-sensor` can be repeated. If any metric expected from those sensors is
missing, the run is marked as failed and the command exits with code `1` after
writing artifacts. Firmware aliases are normalized for presence checks, so `P1`
counts as `pm10`, `P2` counts as `pm25`, `noiseAvg` counts as `noise_avg`, and
`noiseMax` counts as `noise_max`.

For non-standard builds, use `--expect-metric` directly:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-metric temperature \
  --expect-metric humidity \
  --expect-metric pressure
```

`--expect-sensor` and `--expect-metric` can be combined. If neither is
configured, the run is still completed, but `summary.json` records a warning
because the tester cannot know which sensors are mandatory for that device
build.

`--config` loads a TOML tester profile. Supported sections are:

- `[expect]` for required `sensors` and `metrics`;
- `[sensor_ranges.<metric>]` for sane min/max values;
- `[range_checks]` for unknown range behavior;
- `[flatline]` for stuck value thresholds;
- `[cadence]` for update interval thresholds;
- `[serial]` for serial silence thresholds.

CLI `--expect-sensor` and `--expect-metric` values are added to expectations
from the profile.

The tester also checks whether parsed sensor values are stuck:

- each `(sensor, metric)` series should have at least two distinct values;
- one value or a short flatline is reported as a warning;
- a flatline lasting 60 minutes or longer fails the run.

The tester checks sensor update cadence as well:

- the default expected update interval is 5 minutes;
- a gap of 10 minutes or more is reported as a warning;
- a gap of 20 minutes or more fails the run.

Development metrics are checked for reboot signals:

- `uptime_sec` must not decrease during the run;
- `boot` counter must not increase during the run;
- an initial `boot` counter greater than `1` is recorded, but does not fail the
  run by itself.

Serial output is checked for silence:

- 2 minutes without serial lines is reported as a warning;
- 10 minutes without serial lines fails the run;
- if no lines appear at all, the finding mentions that the device may still be
  in Wi-Fi AP/config portal mode.

All health checks are evaluated through a central rules engine. It records a
machine-readable `rules` block with a `PASS_CANDIDATE`, `WARN`, or `FAIL`
verdict plus normalized findings. `FAIL` findings fail the run; `WARN` findings
are preserved for diagnostics without changing the command exit code.

## Run Artifacts

Each run creates a directory under `runs/` by default:

```text
runs/<timestamp>_<port>/
  serial.log
  events.jsonl
  samples.jsonl
  summary.json
  report.txt
```

Important files:

- `serial.log` contains raw serial output as close as possible to the firmware
  output.
- `events.jsonl` contains structured tester events, dev metrics, keyword
  alerts, and sensor presence results.
- `samples.jsonl` contains parsed sensor values.
- `summary.json` contains the machine-readable run result.
- `report.txt` contains a human-readable verdict, findings, health metrics,
  sensor summary, and artifact paths.

`summary.json` includes `rules`, `sensor_presence`, `sensor_ranges`,
`sensor_flatlines`, `sensor_cadence`, `runtime_counters`, and `serial_silence`
sections with observed metrics, missing metrics, warning counts, failure counts,
and detailed findings.

Top-level `summary.json` fields also include `verdict`, `metrics_seen`,
`samples_seen`, and normalized `findings` so automation can read the final
result without traversing every rule-specific section.

While a run is active, the CLI prints live progress with elapsed time and
counters for serial lines, bytes, current serial silence, dev metrics, sensor
samples, and keyword alerts.

## Current Notes

- The default baud rate is `115200`.
- Durations support values such as `30s`, `10m`, `2h`, and `24h`.
- If the device is still in Wi-Fi setup/AP mode, current firmware may not print
  useful serial logs. Configure Wi-Fi before using the run as a burn-in signal.
- Current firmware sensor logs may label pressure as `hPa` while emitting
  Pa-like values. Range checks normalize this format internally.

## Development

```bash
uv sync
uv run altruist-tester --help
uv run python -m altruist_tester.cli --help
uv run pytest
```
