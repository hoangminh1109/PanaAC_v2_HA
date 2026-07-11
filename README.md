# PanaAC v2 — Home Assistant custom integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

MQTT-driven custom integration that exposes a Panasonic AC controller (built with
[`PanaAC_v2_ESPHome`](https://github.com/hoangminh1109/PanaAC_v2_ESPHome)) as a
native Home Assistant `ClimateEntity`.

This integration intentionally avoids the ESPHome native API and the limited
`climate_ir` fan/swing enums. Instead it uses plain MQTT topics, so the climate
card can show the full Panasonic fan levels (`Auto`, `Level 1..5`, `Quiet`),
vertical swing positions (`Auto`, `Highest`, `High`, `Middle`, `Low`, `Lowest`),
and a separate horizontal swing axis (`Auto`, `Left Max`, `Left`, `Middle`,
`Right`, `Right Max`).

## How it works

The ESPHome device publishes:

- `<topic_prefix>/availability` — `online` / `offline` (retained)
- `<topic_prefix>/traits` — supported modes, ranges, temp step (retained)
- `<topic_prefix>/state` — current mode, target temp, fan, swing, horizontal swing (retained)

The integration subscribes to those topics and publishes commands to:

- `<topic_prefix>/set`

Command payloads are partial JSON, e.g. `{"fan_mode": "Level 2"}`. The device
applies only the supplied fields.

## Installation

### Option 1: HACS (recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Open HACS and go to **Integrations**.
3. Click the menu (⋮) and select **Custom repositories**.
4. Add `https://github.com/hoangminh1109/PanaAC_v2_HA` with category **Integration**.
5. Install **PanaAC v2 (MQTT)**.
6. Restart Home Assistant.
7. Add the integration via **Settings → Devices & Services → Add Integration →
   PanaAC v2 (MQTT)**.
8. Enter the MQTT topic prefix configured in the ESPHome YAML
   (`panaac_v2/esphome-panaac-v2` by default).

### Option 2: Manual

1. Make sure the Home Assistant **MQTT** integration is configured and pointed
   at the same broker used by the ESPHome device.
2. Copy or symlink `custom_components/panaac_v2/` into your Home Assistant
   `config/custom_components/` directory.
3. Restart Home Assistant.
4. Add the integration via **Settings → Devices & Services → Add Integration →
   PanaAC v2 (MQTT)**.
5. Enter the MQTT topic prefix configured in the ESPHome YAML
   (`panaac_v2/esphome-panaac-v2` by default).

## Files

```
custom_components/panaac_v2/
  __init__.py      — entry setup, forwards to climate platform
  climate.py       — the ClimateEntity + MQTT subscriptions
  config_flow.py   — config flow (topic prefix)
  const.py         — domain and config keys
  manifest.json    — integration metadata
  strings.json     — UI strings
  translations/en.json
```

## Notes

- The integration depends on the built-in `mqtt` integration
  (`"dependencies": ["mqtt"]`).
- Trait messages are retained, so the climate card gets the correct supported
  modes shortly after HA starts even if the device is currently quiet on the
  state topic.

## More documentation

- [`DESIGN.md`](DESIGN.md) — architecture, MQTT topic contract, HA entity design.
- [`INSTALL.md`](INSTALL.md) — step-by-step installation, broker setup and
  troubleshooting.
