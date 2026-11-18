# Sensor Log Patterns

Full sensor JSON and detailed acceptance/protocol tags require debug. Release
has operational BUILD/LOG records for support, without sensor values, PAYLOAD
samples or periodic HEALTH. LOG operational events/status are not a measurement
source. Runtime/forced verbosity and Testing cannot enable detailed diagnostics
in release. BME680/SCD4x snapshots end with a newline.

BME680 and SCD4x have a minimum 300-second fetch interval. Choose a capture
window that covers the actual configured interval with a margin.

Use [safe diagnostic extraction](diagnostic-procedures.md#safe-extraction-without-devices)
for model-facing summaries. It reuses the UART parser but emits only fixed metric
names/counts and validated identity/status fields; unknown keys, raw_fields,
credentials and entire matching lines are never summary fields.

JSON examples are available in `tests/fixtures/sensor_json_snapshots.log`.

## Full Sensor JSON Snapshot

Observed serial shape:

```json
{"service_data":{"robonomics_address":"...","signal_strength":-38},"BME280":{"humidity":{"value":65.98633,"intl_name":"humidity","units":"%"},"temperature":{"value":25.51055,"intl_name":"temperature","units":"°C"},"pressure":{"value":101069.1,"intl_name":"air pressure","units":"hPa"}},"ICS43434":{"noiseMax":{"value":84,"intl_name":"max noise","units":"dB"},"noiseAvg":{"value":81,"intl_name":"mean noise","units":"dB"}},"SDS":{"P1":{"value":16.33125,"intl_name":"PM10","units":"ppm"},"P2":{"value":7.33125,"intl_name":"PM2.5","units":"ppm"}}}
```

Firmware source:

- `sensors/bmx280i2c_sensor.cpp`
- `sensors/bmx680i2c_sensor.cpp`
- `sensors/i2s_noise_sensor.cpp`
- `sensors/sds011_sensor.cpp`
- `sensors/scd4x_sensor.cpp`
- `sensors/radsens_sensor.cpp`
- `sensors/tiny_gps_sensor.cpp`
- `sensors/http_altruist_sensor.cpp`

Those sensor fetchers update the shared `sensors_data` document. Full UART
JSON serialization is guarded by `ALTRUIST_BUILD_DEBUG`; it is not a release reading contract.
The common measurement shape is:

```json
{
  "<sensor>": {
    "<metric>": {
      "value": 123.4,
      "intl_name": "...",
      "units": "..."
    }
  }
}
```

Urban JSON sensor fields include:

- `BME280.humidity`, unit `%`
- `BME280.temperature`, unit `°C`
- `BME280.pressure`, unit `hPa`
- `ICS43434.noiseMax`, unit `dB`
- `ICS43434.noiseAvg`, unit `dB`
- `SDS.P1`, unit `ppm`, `intl_name` `PM10`
- `SDS.P2`, unit `ppm`, `intl_name` `PM2.5`
- `service_data.signal_strength`, no measurement object

Other firmware-supported sensors may appear in the same JSON shape:

- `BMP280`
- `BME680`
- `RadSens`
- `GPS`
- `SCD4x`
- `altruist_urban`
- `ZMOD4510`
- `AGS3871`

Parser guidance:

- The parser searches for JSON objects within UART lines, including after
  text prefixes and when several objects are concatenated.
- Treat `service_data` as metadata, not a regular sensor sample, unless a
  later rule explicitly needs RSSI from `signal_strength`.
- For normal sensor objects, emit one sample per metric object that has a
  finite numeric `value`.
- Keep sensor and metric names as printed by firmware at this step.

Current Insight BME680 JSON also carries numeric `voc_ppb`, `voc_index`,
`voc_level`, `voc_spikes_today`, `gas_resistance`, and `gas_baseline`, plus text
`voc_phrase`. The numeric parser preserves all six and ignores the phrase,
including preserving `voc_level=-1` and zero gas values for diagnosis. Resistance/baseline
use Ohm; the qualitative VOC index is not concentration in ppm.

BME680 firmware (`sensors/bmx680i2c_sensor.cpp`) emits integer
`voc_ppb` 0–10000 with unit `ppb`: a relative MOS/ABC scale, not laboratory
TVOC. Index 0–100 and level 0–3 derive from that scale; daily counter 0–65535
is also integer. VOC fields are not mandatory in the Insight presence preset.
Default range checks fail values outside these bounds and fractional values.
Level -1 is
an unavailable-reading sentinel and produces WARN; it is not warmup. No elapsed-time
warmup inference is possible from partial UART capture.

The four BME680 VOC scale/indicator/counter series need not vary. Constant zero or
nonzero values pass flatline checks with enough samples; gas resistance and
baseline retain existing diagnostic checks. Cadence checks still apply to all
observed series. The daily counter saturates at 65535, resets by the device's
local date (including time synchronization), and is persisted on SPIFFS. It
may survive reboot or decrease; the tester does not infer a device day from
host timestamps or require monotonicity. Runtime reboot checks still apply.
See [README](../README.md) for the acceptance policy.

## Physical sensor presence

Physical sensor presence checks require every preset metric from that sensor's
own JSON object (with sensor/metric aliases normalized). For example, SCD4x
temperature/humidity and imported `altruist_urban` pressure cannot confirm BME680;
imported or aggregated readings cannot confirm Urban BME280. Missing pairs fail
acceptance and appear as `missing_sensor_metrics` in `sensor_presence` and in
the report message. `missing_metrics` still lists globally absent metric names.
Versioned `_observations` records are the only acceptance measurement source.
Explicit `expect.metrics` alone accepts measurements from any physical sensor;
an empty expectation set warns. Source labels do not prove hardware initialization.
Freshness and deduplication are checked separately.

## Health Contract

Observed serial shape:

```text
[HEALTH] uptime=3600 boot=4 heap=219584 rssi=-62 tx=12 errors=0 wifi=1 wifi_errors=0 sensor_errors=0 sd_errors=0 reset_reason=power_on_reset reset_code=1 crash_valid=0 prev_uptime=0 prev_heap=0 last_section_id=0 last_section=Idle/MainLoop
```

Parser guidance:

- Require the base runtime fields: `uptime`, `boot`, `heap`, `rssi`, `tx`,
  `errors`, `wifi`, `wifi_errors`, `sensor_errors`, and `sd_errors`.
- Parse reset-context fields when present.
- Do not drop the whole health snapshot only because reset-context fields are
  missing.
- Let the UART log contract warn separately when reset context is absent
  from both `[BOOT]` and `[HEALTH]`.

## Build Contract

Observed serial shape:

```text
[BUILD] version=R-URB_2026-07-08-testing+abc1234 channel=testing commit=abc1234 model=urban target=esp32c6 language=en profile=release
```

Parser guidance:

- Store `version`, `channel`, `commit`, `model`, `target`, `language`, and
  `profile` in run summary/report.
- Treat `channel` as firmware provenance (`stable` or `testing`), not as a
  verbosity switch.
- Treat `profile` as build-mode context (`release` or `debug`), not as a
  requirement for acceptance tests.

## Payload Contract

Observed serial shape:

```text
[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=56
[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324
[PAYLOAD] channel=sensors-connectivity encoding=mixed encrypted=1 payload_len=280
```

Firmware source:

- `apis/helpers/message_formatter.cpp`

The firmware formats payloads from `sensors_data`, applying sharing/encryption
flags and selecting temperature/humidity sources where applicable. The tester
records `channel`, `encoding`, `encrypted` and `payload_len` as transport
metadata. PAYLOAD lines contain no sensor values or sample-availability field.
Transport bodies are not measurement inputs. Ciphertext, signatures and
`[PROTO]` are not sensor readings.
Acceptance measurements come only from versioned debug UART observations.


## Malformed PAYLOAD diagnostics

Only `datalog`, `sensors-connectivity`, and `custom-http` are known PAYLOAD
channels. Missing/empty channels (including a bare `[PAYLOAD]`) are classified
as `missing_channel`; other names, including truncated names, as `unknown_channel`.
These records do not contribute to normal payload counts, channel/encoding
statistics, observation records or sensor samples. `custom-http` remains a valid
metadata observation without compact sensor readings. Versioned sensor observations are handled independently of payload metadata.

`summary.json` adds `malformed_payload_observations_count`,
`malformed_payload_observations_by_reason`, and
`malformed_payload_observation_examples` inside `payload_observations`.
The report shows the malformed count; the diagnostic itself adds no verdict
severity and CLI exit codes remain 0/1/2. The first five malformed records also
produce `malformed_payload_observation` events. Each example includes its UTC
time, one-based line number in `serial.log`, reason, allowlisted metadata and
an explicit `truncated` flag; values are capped at 128 characters and each
serialized example at 1 KiB. Fields that exceed the total budget are omitted.
Samples, transport bodies, signatures and unknown fields are not copied into
examples. Further malformed records only increment counters. The complete raw
UART is preserved unchanged in `serial.log`; old artifacts are not rewritten.

## Map server selection and upload outcomes

Map server health GET probes are server-selection diagnostics, not data uploads.
`[CONNECTIVITY] ... stage=health` records are retained in
`events.jsonl` (including `raw_fields`) and counted separately as `health_checks`
and `health_check_reasons` in upload summaries/reports. They do not affect upload
attempts, success rate, failure streaks, or delivery targets. A failed health GET
followed by a successful POST is one successful upload (100%). Health probes
alone cannot satisfy an enabled upload check. Real POST/local failures and
unknown reasons retain the configured required/optional/disabled behavior;
results sharing a sequence number are not merged across transport attempts.

`health_checks` counts parsed structured health events. Current firmware emits
these events only for failed probes; successful GETs remain in verbose Map logs.
A zero counter therefore does not mean that no health GET was performed.

## Runtime keyword alerts

Only these complete EPD maintenance lines are exempt from WATCHDOG matching
(case-insensitive, with surrounding whitespace ignored and an optional exact
`[DEBUG] ` prefix emitted by the firmware logger):

```text
[EPD] FULL refresh (watchdog/wake/main) pushed to panel
[EPD] Periodic watchdog: recovering display and scheduling FULL refresh
```

Changed messages or appended failure text retain normal detection. This is not
an exemption for EPD logs in general. Task/interrupt watchdog failures, ESP WDT
boot reset reasons and project watchdog restarts remain severity=fail, as do
other crash/reset keywords. Alerts appear in events.jsonl and summary.json;
the rule engine includes them as `keyword_alerts` findings in summary/report
and the final FAIL verdict (CLI exit 1). Raw serial.log bytes are unchanged.

## Versioned debug UART observations

Debug firmware extends the existing sensor/metric/value/units JSON shape with
`_observations` at the root and `observation` per numeric metric. Each line is
one snapshot of **one physical sensor**, not a promise to deliver every reading.
Only debug emits this schema, independently of encryption, connectivity and
protobuf success. Metadata is assembled in a separate UART document; API,
uplink and SD data retain their existing shape. BME280/BMP280, BME680, SCD4x,
SDS and ICS43434 are supported; experimental radiation does not emit this schema.

```json
{"BME280":{"temperature":{"value":24,"units":"°C","observation":{"seq":"7","measured_ms":"300010","state":"ok"}}},"_observations":{"version":1,"device_id":"1051DB010C70","boot_id":"1a2b-3c4d","model":"urban","sensor":"BME280","snapshot_seq":"12","time_ms":"330000","state":"ok","errors":0,"reason":"none"}}
```

Required root metadata: version 1; twelve hexadecimal device-id digits; a
per-boot random token of two 1–8 digit hexadecimal components separated by `-`;
model `urban`/`insight`; physical sensor; increasing per-sensor snapshot sequence;
monotonic milliseconds since boot; state; cumulative per-sensor error count and
stable error reason. Context repeats in every snapshot for late attachment.
The random token changes on reboot; a context change during capture fails the
contract check even if HEALTH/BOOT was missed. The token is observational identity,
not authentication. Device identity must still match expected USB/UART identity.

Clock and sequence fields are emitted as decimal strings to preserve 64-bit
precision without changing ArduinoJson globally. The parser also accepts exact
JSON integers. It validates clocks through 2^42−1 ms and sequences through
2^53−1; booleans and negative counters are invalid. Device clocks use
`esp_timer_get_time`, so they do not wrap with 32-bit `millis()`. Numeric values
must be finite (integer values within ±(2^53−1)); original units are preserved,
including legacy BME280 pressure labeling and current SDS µg/m³.

Each numeric metric carries its successful update sequence, successful update
milliseconds, and state. New equal values increment the sequence. Reprinting
or a failed read preserves successful identity/time. Errors mark cached metrics
as failed and increment the sensor error count; partial invalid numeric values
can mark one metric failed. `voc_level=-1` retains the unavailable-reading
WARN, and VOC indicator/counter flatline exceptions remain unchanged. The schema
accepts `never`, `warming`, `waiting`, `ok`, `error` sensor states and all except
`never` for metrics with a successful identity. Current drivers emit `never`
before a first result, `ok` after updates and `error` on instrumented failures;
ordinary SDS sleep/noise pause does not create an error. This does not infer VOC
algorithm readiness from elapsed time.

SDS updates represent completed measurement windows (with existing trimmed-mean
processing). Noise values are running aggregates of the current sending window:
sequence/time advance on each successful aggregate update, not merely on UART
publication, and reset of the aggregate does not reset sequence. Noise snapshots
can therefore represent partial windows. BME680 gas and VOC values are derived
from the same successful fetch. Individual metric times remain separate.

A heartbeat is checked by the sensor worker: nominally every 30 seconds per
active supported sensor, also at first observation and after a reported error.
Blocking existing sensor/network work may delay it; this is not a hard realtime
guarantee. At most 16 metric fields per sensor and 8192 UTF-8 bytes per line are
accepted. Firmware builds an 8192-byte temporary JSON document, then writes the
serialized line in one call. Overflow emits an invalid observation marker and
fails acceptance instead of producing a partial PASS. Snapshot sequence gaps
and skipped metric updates are counted separately; snapshots cannot establish
absence of intermediate errors or capture every physical sample. Sensors that
fail initialization may be absent entirely; expected physical presence remains
the tester profile's responsibility.

`serial_observation_v1` is the only acceptance measurement source for the entire
capture. Missing `_observations` telemetry produces `SENSOR_OBSERVATION_MISSING`
and FAIL/exit 1. Malformed records fail the contract; ordinary sensor JSON and
PAYLOAD samples cannot fill gaps or refresh measurements. Full sensor JSON is
parsed only by diagnostic extraction commands.

Deduplication uses device/boot/sensor/metric/measurement sequence. A repeated
measurement is not appended even in a later snapshot; a new equal value is.
Regressed/inconsistent sequence/time/value, dropped metric fields, conflicting
identity, unknown version/state, malformed/oversized records or reboot produce
FAIL findings. Unknown extension fields are ignored and cannot override validated
fields. No nested ordinary JSON values are salvaged from malformed versioned records.

`events.jsonl` stores sanitized sensor_observation events, including error/cache
states. `samples.jsonl` adds an optional `observation`
object with device/boot identity, sequence, device snapshot/measurement times,
state and projected `measured_ts`; its `ts` remains host receipt time. A fixed
per-boot mapping, established on first observation, projects device milliseconds
onto host UTC for cadence/flatline; it is not a synchronized device UTC clock.
`summary.sensor_observations` records
source, snapshot/duplicate/loss counters, issues, latest sensor states and metric
ages. `sensor_samples_count` counts the accepted measurement records written
to samples.jsonl; cache repeats do not create new records.

Freshness uses device age at the latest snapshot plus host time since receipt.
Warn/fail thresholds reuse the tester's configured cadence expected interval and
missed-count multipliers, rather than the 30-second publication interval. Current
read errors fail; cumulative recovered/pre-capture errors warn without asserting
when they occurred. These findings participate in the usual report and CLI 1.
Delivery rules remain independent: local readings never confirm remote delivery.
