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
the report message. `missing_metrics` lists globally absent metric names;
`missing_sensor_metrics` lists every unconfirmed physical sensor/metric pair.
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
- Parse reset-context fields when present. Missing or invalid optional numeric
  and boolean values remain `null`; zero and `false` are retained.
- Do not drop the whole health snapshot only because reset-context fields are
  missing.
- Let the UART log contract warn separately when reset context is absent
  from both `[BOOT]` and `[HEALTH]`.

HEALTH events and `last_dev_metrics` contain runtime/link counters and optional
reset/crash context. HEALTH has no model, last-transmission age, or ESP
temperature field; these are not emitted as empty event fields or summary
temperature extrema. Device model comes from BUILD and identity observations.

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

Acceptance requires `_observations.version` as the JSON integer `2` and uses
only `serial_observation_v2`. One line describes one physical sensor, independent
of uplink, API and SD. Ordinary full JSON remains available to diagnostic
extraction. Version 1, missing observations and malformed records cannot refresh
acceptance measurements through another source. BUILD/BOOT/HEALTH and delivery
checks remain independent. Debug is required for acceptance measurements.

The metadata repeats device_id (12 hexadecimal digits), boot_id (two 1–8 digit
hexadecimal components separated by `-`), model (`urban`/`insight`), sensor,
snapshot_seq, time_ms, cycle_seq, state, reason, settings, channels and failures.
Sensor names are BME280/BMP280/BME680/SCD4x/SDS/ICS43434. Context changes during
capture fail. Snapshot sequence starts at one and advances per sensor even when
a generated line is lost. cycle_seq counts completed external cycles. The old
`errors` field is invalid; failed cycles are separate from diagnostic messages.
An explicit `snapshot_overflow` marker fails without salvaging measurements.

All clocks and counters accept decimal strings or exact JSON integers, never
booleans or floats. Clocks are monotonic boot milliseconds through 2^42−1,
counters through 2^53−1. Negative/future values and duplicate JSON keys fail.
Limits are 8192 UTF-8 bytes per line and 16 metric fields per sensor. Unknown
extensions are ignored and omitted from structured artifacts; known fields are
validated atomically. No nested values are salvaged from malformed records.

Each known numeric metric has value, optional units and observation with seq,
measured_ms and state. Values must be finite, with integers within ±(2^53−1).
Before its first success a metric has null value/time and seq=0; its state can
be never/warming/waiting/error. This is evidence of state, never a sample.
After success, value/time and seq>=1 remain cached during errors/waits. New equal
values advance identity; a duplicate does not create a sample. An identity may
not change its value/unit/time. New metric identities require successful channel
cycles. Display strings and unknown metric extensions are not samples.

Required metrics (even before first success):

| Sensor | Metrics | Channels |
| --- | --- | --- |
| BME280 | temperature, humidity, pressure | main |
| BMP280 | temperature, pressure | main |
| BME680 | temperature, humidity, pressure; gas_resistance, gas_baseline, voc_ppb, voc_index, voc_level, voc_spikes_today when gas enabled | environment, gas |
| SCD4x | temperature, humidity, co2 | main |
| SDS | P1, P2 | main |
| ICS43434 | noiseAvg, noiseMax | main |

Every channel has cycles_total=ok_total+failed_total, last_ok_ms,
last_failed_ms, last_recovered_ms, state and reason. Timestamps are null before
an event. Counters and timestamps cannot regress. Init errors can have zero
cycles. A failed channel cannot clear to waiting/ok without a new successful
cycle. BME680 environment success remains a sample when gas fails; errors do
not multiply by the number of metric fields. Global state is error while a
channel is in error. Stable reason codes use `[a-z0-9_]{1,64}`; ok uses none,
error requires a non-none reason. Supported states are
never/warming/waiting/ok/error. Waiting/sds_active is not an error.

`failures` contains next_seq and recent, a complete contiguous tail of up to 16
records. Each record has seq, cycle_seq, at_ms, a nonempty unique channels list
and reason. Failure sequence is global per sensor/boot; a shared BME680 failure
has both channels in one record. The parser validates history against channel
failed counters and times, including deltas between snapshots, and rejects
rewritten repeated records. Tail eviction preserves counters without inventing
unseen times. These records are evidence, not an unlimited error journal.

### Expected effective settings

Every UART settings object contains all of these keys:

| Key | Meaning |
| --- | --- |
| fetch_wait_ms | Positive effective wait before external calls |
| measurement_interval_ms | BME/SCD >=300000; SDS >=30000; noise null |
| publication | on_update, or coalesced for noise |
| publish_interval_ms | 0, or 30000 for noise |
| state_min_interval_ms | 1000 |
| gas_enabled | Boolean for BME680; null otherwise |
| sds_warmup_ms / sds_collect_ms | 15000/5000 for SDS and noise with SDS, 0/0 for noise without SDS; null otherwise |
| noise_window_active_ms | 300000 for noise; null otherwise |

The tester compares these with explicit `[observations.settings.SENSOR]` tables
in the selected TOML profile. Null fields are omitted in TOML, not in UART.
Canonical sensor names are case-sensitive. Profiles also require nonempty
`observations.firmware_source` and `observations.settings_rule`. Expectations
are fixed before capture. Unknown profile setting names fail config validation.
A missing expectation, valid-but-different settings or a settings change stops
capture with CLI 2. This records a setup problem without a device-defect verdict.
Malformed declarations, missing required fields and counter contradictions are
contract failures (CLI 1). Unknown BUILD is retained without inferring a local
firmware revision. Credentials and transport URLs do not belong in settings.

### Noise evidence

ICS43434 additionally requires noise with started_ms, successful_blocks_total,
active_ms_total, last_ok_ms, sds_pause, interval, windows and window_current.
Successful blocks equal main.ok_total. Active time equals elapsed time since
subsystem start minus bounded excluded SDS time; it grows during failures too.
`sds_pause` has cycle_seq, state active/idle, started_ms (nullable) and
excluded_ms_total. One SDS cycle cannot exclude more than warmup+collect.
Repeated/reentered cycles cannot renew this allowance. Without SDS the pause
has zero sequence/exclusion and null start. Unseen pause boundaries are not
proof of exact per-interval exclusion.

`interval` contains start_ms, end_ms, max_gap_active_ms, max_gap_end_ms and
open_gap_active_ms. End equals snapshot time. A consecutive snapshot's interval
starts at the previous snapshot; a snapshot gap cannot be replaced with an
invented zero interval. Open gaps include time since subsystem start before
the first success. Repeated completed windows are immutable.

`windows` holds the two last completed consecutive non-overlapping windows:
seq, start_ms, end_ms, active_ms=300000, successful_blocks. `window_current`
is mandatory and has seq, start_ms, active_ms<300000 and successful_blocks.
Window sequences and active time must agree, adjacent boundaries must meet,
and counts cannot exceed cumulative successes. Windows describe statistics of
reads; they do not produce additional noiseAvg/noiseMax samples.

### Events and reports

`events.jsonl` stores sanitized sensor_observation events including state-only
records, channel counters, history, settings and noise evidence.
`samples.jsonl` contains only new successful metrics, with observation identity,
snapshot/measurement times and projected measured_ts. Its ts is host receipt.
The fixed boot mapping projects monotonic device time onto host time; it is not
synchronized UTC. `summary.sensor_observations` and the UART report section
preserve expected_settings, profile_rules, profile_error, issues, accepted
sensor declarations, duplicate/loss counters and latest metric ages. A rejected
profile declaration is retained inside profile_error.declared and its event.
BUILD is saved separately even when unknown or absent.

The acceptance ledger in `summary.sensor_observations.acceptance` preserves
per-boot/sensor baselines, capture/historical/uncertain failed-cycle counts,
rolling failure timestamps and retained findings. Recovery cannot erase a
proven budget violation. At first receipt, host elapsed time and device time
estimate the capture boundary; failures whose attribution is uncertain give
coverage WARN. Only counter increases after the accepted baseline spend the
capture budget. Repeated history is deduplicated. A missing bounded-tail prefix
keeps its counter delta, but its exact times are never invented.

Slow-metric cadence uses the explicit profile's `measurement_interval_ms` T.
Gaps and freshness WARN at >=T+60 seconds and FAIL at >=2T+300 seconds. First
success WARNs at >=T+60 and FAILs at >=T+300. Host time extends the last age
without a UART snapshot; HEALTH does not refresh sensor evidence. At least
three distinct successful capture identities per mandatory metric are needed
for a positive cadence result. One or two give coverage WARN. Historical
cache does not count. A sequence jump records incomplete coverage instead of
claiming the full span was a physical measurement gap. Observed delayed
successes and stale cache retain their gap evidence after recovery.

One recovered failed external cycle per channel gives WARN; two within any
rolling 86400000 device milliseconds give sticky FAIL. Gas is independent of
BME680 environmental readings. Repeated fields/records and internal successful
retries do not multiply failures. An unrecovered error at the end fails even
with a single failure or no completed init cycle. Historical errors warn
outside the capture budget; active historical errors still fail. Lost times
produce coverage WARN unless retained timestamps or interval/counter bounds
already prove a breach. Error percentages are not computed from snapshots.

Only complete microphone windows starting at/after the accepted microphone
baseline establish frequency. Each is 300000 active milliseconds: frequency
<2 Hz WARN, <1 Hz FAIL. Historical/partial or missed windows leave coverage
incomplete, and an observed failure remains. Gaps independently WARN at
>=60000 and FAIL at >=300000 active milliseconds. The open gap continues on
host time after the latest snapshot. An active SDS pause grants only its
remaining warmup+collection allowance, capped at 20000 milliseconds per cycle;
repeated/reentered cycle state cannot renew it. The temporal validator rejects
a reset open gap without new successful blocks, including across snapshots.
Noise-window statistics never become metric samples.

Known sensor read/init SUBSYSTEM reasons corroborated by accepted v2 evidence
use the ledger's recovery/budget decision. Unmatched and contract/overflow
errors retain FAIL. Sensor first-wait windows take precedence over the common
sensor-sample startup deadline; independent HEALTH/BUILD/BOOT rules remain.
The profile rejects global `[cadence]` settings; effective intervals are explicit
per sensor. Delivery checks remain independent of local sensor measurements.


### Acquisition probes and limited activity profiles

The optional known `_observations.probes` object declares acquisition-only
channels: exactly `gas` for BME680 or `main` for ICS43434. Other sensors cannot
claim this mode. Each declared channel remains `waiting/driver_unverified` with
zero measurement cycles/ok/failed and null, seq=0 measurement fields. This is
separate from measurement success and independent of the device's computed
API/uplink/SD values.

Each probe contains `attempts_total`, `transfers_total`, `activity_total`,
`last_attempt_ms`, `last_transfer_ms`, `last_activity_ms`, `result`, `error_code`,
`bytes_read`, `expected_bytes`, `signal_min`, `signal_max`, `gas_status`,
`gas_resistance`. Counters and clocks use the normal exact integer encoding;
event times are null before the event. Counters satisfy activity <= transfers
<= attempts and are cumulative across publications. Result is `never`,
`init_failed`, `read_failed`, `short_read`, `no_new_data`, `no_activity` or
`activity`. Details describe the latest attempt; cumulative times retain the
latest corresponding event. Probe changes without a new attempt, regressions,
inconsistent details and simultaneous measurement success in that channel fail
atomic validation. Probe channel declarations cannot change during capture.

Microphone evidence carries the raw I²S return code and byte count; a complete
block is 8192 bytes. Signal extrema are signed 24-bit samples before FFT and are
null on unsuccessful/incomplete transfers. Gas details are null without new
transferred data; otherwise gas_status is the raw 8-bit status and resistance
is the raw unsigned resistance. The heater-stable bit is 0x10 and gas-valid bit
is 0x20. Flags are recorded even when the reading is only activity evidence;
none of these probes advances gas/VOC/noise measurement identities.

A profile may explicitly set `observations.activity_only = ["BME680.gas"]`
or `["ICS43434.main"]`, or both when both channels are expected. Unique entries
and explicit effective settings for each selected sensor are required. Defaults
remain full acceptance. Device probes with no matching operator selection fail
full acceptance; a selected channel with missing probe evidence also fails.
No parser fallback or automatic downgrade is permitted.

The first receipt establishes each probe's activity baseline. A later increased
activity counter and successful latest attempt are required. Duplicate frames
or old activity alone do not pass. Limited success is always WARN with the
measurement-validity limitation. Latest failed/no-activity acquisition and stale
activity fail; thresholds are 300 s for I²S activity and 2*T+300 s for gas,
including host tail. The selected physical channels are excluded from
measurement presence/cadence; their null values never enter ranges/flatline.
Other channels, including BME680 environment, keep full acceptance rules.
Expected settings mismatch/change still stops capture with CLI 2.
