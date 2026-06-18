# Altruist Tester

Python burn-in tester for assembled Altruist devices.

The current workflow tests one device connected over USB-C serial to a Linux
host, Raspberry Pi, or development machine. The tester captures raw firmware
logs, parses development metrics and sensor values, evaluates health rules, and
writes machine-readable and human-readable run artifacts.

## Quick Start

Install dependencies:

```bash
uv sync
```

List detected USB serial ports:

```bash
uv run altruist-tester ports
```

Run a short test using the only detected serial port:

```bash
uv run altruist-tester run --auto --duration 10m
```

Run against an explicit port:

```bash
uv run altruist-tester run --port /dev/ttyACM0 --duration 10m
```

Use `--output-dir` to write artifacts somewhere other than `runs/`:

```bash
uv run altruist-tester run --auto --duration 24h --output-dir /data/altruist-runs
```

## Device Profiles

Use a profile when testing a known Altruist build. Profiles define expected
sensors and rule thresholds.

Urban:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/urban.example.toml
```

The Urban profile expects:

- `bme280`: temperature, humidity, pressure;
- `sds`: PM10 and PM2.5;
- `ics-43434`: average and max noise.

Insight:

```bash
uv run altruist-tester run --auto --duration 24h \
  --config configs/insight.example.toml
```

The Insight profile expects:

- `scd41`: CO2, temperature, humidity;
- `bme680`: temperature, humidity, pressure.

The firmware currently prints the SCD sensor as `SCD4x`; the tester accepts
`scd41`, `scd40`, and `scd4x` as the same expected sensor preset.

For ad-hoc checks, pass expectations directly:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-sensor bme280 \
  --expect-sensor sds \
  --expect-sensor ics-43434
```

You can also require individual metrics:

```bash
uv run altruist-tester run --auto --duration 10m \
  --expect-metric temperature \
  --expect-metric humidity \
  --expect-metric pressure
```

`--expect-sensor` and `--expect-metric` can be repeated and can be combined with
`--config`. CLI expectations are added to expectations from the config file.

If no expectations are configured, the run can still complete, but
`summary.json` records a warning because the tester cannot know which sensor
metrics are mandatory for that device.

## Health Checks

The tester evaluates all health checks through one rules engine. The final
verdict is written as:

- `PASS_CANDIDATE`: no warnings or failures;
- `WARN`: diagnostics need attention, but the command exits successfully;
- `FAIL`: one or more checks failed, and the command exits with code `1`.

Presence:

- checks that all expected metrics were observed at least once;
- normalizes firmware aliases for presence checks, for example `P1` as `pm10`,
  `P2` as `pm25`, `noiseAvg` as `noise_avg`, and `noiseMax` as `noise_max`.

Sensor ranges:

- checks parsed values against sane default or configured ranges;
- normalizes pressure emitted as `Pa` or Pa-like `hPa` into `hPa` before range
  checks.

Flatline detection:

- checks each `(sensor, metric)` series for enough value variation;
- short or inconclusive flatlines are warnings;
- flatlines lasting `flatline.fail_after` fail the run.

Update cadence:

- checks whether each parsed metric updates regularly;
- by default, the expected interval is 5 minutes;
- warnings and failures are configured as missed-interval multipliers.

Runtime counters:

- checks that development-metrics uptime does not decrease;
- checks that the boot counter does not increase during the run;
- records an initial boot counter greater than `1`, but does not fail the run
  for that alone.

Serial silence:

- warns when serial output is silent for too long;
- fails when silence reaches the configured failure threshold;
- reports a dedicated finding when no serial lines appear at all.

Keyword alerts:

- watches raw serial lines for panic, watchdog, brownout, unexpected restart,
  CPU lock-up, power glitch, eFuse error, assertion, stack, heap, and access
  fault patterns.

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

- `serial.log` is the raw UART capture. Keep it as the source of truth when
firmware output or parsers need investigation.

- `summary.json` is the main machine-readable result. It contains the verdict,
top-level findings, counters, and per-rule sections:
  - `rules`;
  - `sensor_presence`;
  - `sensor_ranges`;
  - `sensor_flatlines`;
  - `sensor_cadence`;
  - `runtime_counters`;
  - `serial_silence`.

- `report.txt` is the human-readable run report for quick inspection over SSH or
for pasting into an issue or chat.

- `samples.jsonl` contains parsed sensor samples with tester-side timestamps. It
is useful for graphs, cadence analysis, flatline debugging, and parser checks.

- `events.jsonl` contains chronological tester events, parsed development metrics,
and keyword alerts.

While a run is active, the CLI prints live progress with elapsed time, serial
line and byte counters, current serial silence, parsed dev metrics, parsed
sensor samples, and keyword alert count.

## Config Files

`--config` loads a TOML tester profile. Supported sections:

- `[expect]` for required `sensors` and `metrics`;
- `[sensor_ranges.<metric>]` for sane min/max values;
- `[range_checks]` for unknown metric behavior;
- `[flatline]` for stuck-value thresholds;
- `[cadence]` for update interval thresholds;
- `[serial]` for serial silence thresholds.

Durations in config files use the same format as CLI durations: `30s`, `10m`,
`2h`, or raw seconds as a positive integer.

Example:

```toml
[expect]
sensors = ["bme280", "sds", "ics-43434"]
metrics = []

[flatline]
window = "30m"
fail_after = "1h"
min_distinct_values = 2

[cadence]
expected_interval = "5m"
warn_after_missed = 2
fail_after_missed = 4

[serial]
silence_warn_after = "2m"
silence_fail_after = "10m"
```

## Firmware Notes

- The default baud rate is `115200`.
- Configure Wi-Fi before using a run as a burn-in signal.
- Map or datalog HTTP failures can appear in `serial.log`. They are kept in the
  raw log, but expected network/API failures are not treated as keyword-alert
  runtime failures.

## Exit Codes

- `0`: run completed without `FAIL` findings;
- `1`: run completed and health checks produced `FAIL`;
- `2`: CLI usage, missing port, config error, or serial-open error.

## Development

```bash
uv sync
uv run altruist-tester --help
uv run altruist-tester run --help
uv run python -m pytest
```
