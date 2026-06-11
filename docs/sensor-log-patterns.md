# Sensor Log Patterns

Source run: `runs/2026-06-11T12-27-51Z_ttyACM0`

Run summary:

- duration: `10m`
- serial lines: `2392`
- development metrics blocks: `200`
- keyword alerts: `0`
- observed full sensor JSON snapshots: `17`
- observed compact datalog sensor lines: `13`

The captured examples are stored in
`tests/fixtures/dev_serial_with_sensor_values.log`.

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

Those sensor fetchers update the shared `sensors_data` document and call
`serializeJson(data, Serial)`. The common measurement shape is:

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

- Prefer JSON parsing for lines that start with `{` and contain
  `"service_data"` or known sensor keys.
- Treat `service_data` as metadata, not a regular sensor sample, unless a
  later rule explicitly needs RSSI from `signal_strength`.
- For normal sensor objects, emit one sample per metric object that has a
  finite numeric `value`.
- Keep sensor and metric names as printed by firmware at this step.

## Compact Datalog Line

Observed serial shape:

```text
[1635773] [INFO] Datalog data: : h:65.99,t:25.51,p:101069.09,nm:83,na:81,p1:16.33,p2:7.33
```

Firmware source:

- `apis/helpers/message_formatter.cpp`

The compact line is built by `formatRobonomicsString()` from the same
`sensors_data` document. It applies share flags and may choose one
temperature/humidity source when both `BME680` and `SCD4x` are present.

Observed aliases in the 10-minute Urban run:

- `h`: humidity
- `t`: temperature
- `p`: pressure
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

- Parse this format as a fallback or complementary source.
- The full JSON snapshot is richer because it preserves sensor names and units.
- The compact line is useful when a run has `Datalog data` but no full JSON
  snapshot near the same timestamp.
