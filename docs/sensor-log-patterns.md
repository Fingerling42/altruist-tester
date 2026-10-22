# Sensor Log Patterns

Source run: `runs/2026-06-11T12-27-51Z_ttyACM0`

Run summary:

- duration: `10m`
- serial lines: `2392`
- keyword alerts: `0`
- observed full sensor JSON snapshots: `17`

The captured JSON examples are stored in
`tests/fixtures/sensor_json_snapshots.log`. Historical `Datalog data` lines
are not current compact-reading inputs.

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

Those sensor fetchers update the shared `sensors_data` document. In the local
drivers reviewed at firmware `344f123` (2026-10-22), full UART JSON serialization
is guarded by `ALTRUIST_BUILD_DEBUG`; it is not a release reading contract.
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

Observed sensors and metrics in the 10-minute Urban run:

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

Current Insight BME680 JSON also carries numeric `voc_index`, `voc_level`,
`voc_spikes_today`, `gas_resistance`, and `gas_baseline`, plus text `voc_phrase`.
The numeric parser preserves the first five and ignores the phrase. Generic
flatline rules currently fail constant VOC status/counter values after the
configured threshold; dedicated semantics remain planned. Resistance/baseline
use Ohm; the qualitative VOC index is not concentration in ppm.

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
- Let the release log contract warn separately when reset context is absent
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
[PAYLOAD] channel=datalog encoding=plain encrypted=0 payload_len=56 sample_available=1 sample=h:65.99,t:25.51,p:101069.09,nm:83,na:81,p1:16.33,p2:7.33
[PAYLOAD] channel=datalog encoding=cps encrypted=1 payload_len=324 sample_available=0
[PAYLOAD] channel=sensors-connectivity encoding=mixed encrypted=1 payload_len=280 sample_available=0
```

Firmware source:

- `apis/helpers/message_formatter.cpp`

The payload contract is built from the same `sensors_data` document. It applies
share/encryption flags and may choose one temperature/humidity source when both
`BME680` and `SCD4x` are present.

Observed aliases in the 10-minute Urban run:

- `h`: humidity
- `t`: temperature
- `p`: pressure, emitted in pascals by current payload samples
- `nm`: max noise
- `na`: mean noise
- `p1`: PM10
- `p2`: PM2.5

Additional aliases supported by firmware:

- `gc`: radiation
- `co2`: CO2
- `co`: CO
- `o3`: O3
- `no2`: NO2
- `fa`: fast AQI
- `ea`: EPA AQI

Parser guidance:

- Parse compact sensor values only from optional `sample=...` on
  `channel=sensors-connectivity` or `channel=datalog`.
- Capture accepts datalog samples until the first connectivity sample, then
  suppresses datalog samples for the rest of the run. Full JSON snapshots
  bypass this selection; there is no full cross-source deduplication.
- Treat `custom-http` payloads as observations, not sensor-series input.
- For plain unencrypted samples with parseable `payload_len`, require an exact
  length match before creating readings. Missing/unparseable lengths currently
  bypass that guard.
- Never parse encrypted `e...` values or full transport payloads as sensor
  metrics.
- Treat `channel`, `encoding`, `encrypted`, `payload_len`, and
  `sample_available` as payload metadata.
- Full JSON snapshots preserve physical sensor names and units. Release runs
  need readable samples: encrypted payloads may intentionally omit them, and
  current C6 protobuf success skips legacy payload formatting/logging. A stable
  transport-independent release source remains planned; `[PROTO]` cannot
  replace it.
