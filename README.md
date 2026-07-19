# PanaAC v2 — Home Assistant custom integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

MQTT-driven custom integration that exposes a Panasonic AC controller (built with
[`PanaAC_v2_ESPHome`](https://github.com/hoangminh1109/PanaAC_v2_ESPHome)) as a
native Home Assistant `ClimateEntity`.

In this workspace, the custom PanaAC v2 repositories now live under
`panaac_v2/`, and the consolidated shared test workspace is
`../PanaAC_v2_Testing`.

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

## Automation examples

The entity exposes `hvac_action`, so the climate building-block triggers and
conditions work (`started_cooling`, `is_cooling`, …). These follow the current
Home Assistant YAML style — `triggers:`/`conditions:`/`actions:` lists with
`trigger:`/`condition:`/`action:` keys (the modern names for the legacy
`platform:`/`service:`). Replace `climate.living_room` with your entity id.

**Trigger — thermostat started cooling**

```yaml
triggers:
  - trigger: climate.started_cooling
    target:
      entity_id: climate.living_room
    options:
      behavior: each        # each | first | all (default each)
      for: "00:00:00"        # fires immediately by default
```

**Condition — thermostat is cooling** (gated behind a state change)

```yaml
triggers:
  - trigger: state
    entity_id: climate.living_room
    attribute: hvac_action
    to: cooling
conditions:
  - condition: climate.is_cooling
    target:
      entity_id: climate.living_room
    options:
      behavior: any          # any | all (default any)
```

**Actions — set mode, target temperature, fan, swing**

```yaml
actions:
  - action: climate.set_hvac_mode
    target:
      entity_id: climate.living_room
    data:
      hvac_mode: cool        # off | cool | heat | fan_only | dry | auto

  - action: climate.set_temperature
    target:
      entity_id: climate.living_room
    data:
      temperature: 24
      hvac_mode: cool        # optional; keeps the current mode if omitted

  - action: climate.set_fan_mode
    target:
      entity_id: climate.living_room
    data:
      fan_mode: "Level 2"    # Auto | Level 1..5 | Quiet | Powerful

  - action: climate.set_swing_mode
    target:
      entity_id: climate.living_room
    data:
      swing_mode: Middle     # Auto | Highest | High | Middle | Low | Lowest

  - action: climate.set_swing_horizontal_mode
    target:
      entity_id: climate.living_room
    data:
      swing_horizontal_mode: Left   # Auto | Left Max | Left | Middle | Right | Right Max
```

`turn_on` / `turn_off` / `toggle` take only `target:` (no `data:`) and appear
once the device advertises more than one HVAC mode that includes `off`.

> `hvac_action` is *derived* from the commanded mode (the controller is one-way
> IR and cannot report whether the compressor is actually running), so
> `started_cooling` / `is_cooling` reflect the mode the unit was commanded to,
> not verified compressor activity. See [`DESIGN.md`](DESIGN.md) for the full
> mapping.

## More documentation

- [`DESIGN.md`](DESIGN.md) — architecture, MQTT topic contract, HA entity design.
- [`INSTALL.md`](INSTALL.md) — step-by-step installation, broker setup and
  troubleshooting.
