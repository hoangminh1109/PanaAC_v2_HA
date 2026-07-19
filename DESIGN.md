# PanaAC v2 — Home Assistant integration design document

## Goal

Provide a native Home Assistant `ClimateEntity` for the ESPHome
`PanaAC_v2_ESPHome` controller, exposing:

- full Panasonic fan levels (`Auto`, `Level 1` … `Level 5`, `Quiet`);
- vertical swing positions (`Auto`, `Highest`, `High`, `Middle`, `Low`, `Lowest`);
- a separate horizontal swing axis (`Auto`, `Left Max`, `Left`, `Middle`, `Right`, `Right Max`).

## Architecture

```
┌─────────────────────────────────────────┐
│  Home Assistant frontend                │
│  (climate card / climate services)      │
└──────────────┬──────────────────────────┘
               │
               │ ClimateEntity properties/setters
               │
┌──────────────┴──────────────────────────┐
│  custom_components/panaac_v2/climate.py │
│  PanaACV2Climate(ClimateEntity)         │
│    - subscribed to MQTT topics          │
│    - publishes commands to MQTT         │
└──────────────┬──────────────────────────┘
               │
               │ MQTT publish/subscribe
               │ (via homeassistant.components.mqtt)
               │
┌──────────────┴──────────────────────────┐
│  MQTT broker                            │
└─────────────────────────────────────────┘
               │
               │ MQTT publish/subscribe
               │
┌──────────────┴──────────────────────────┐
│  ESPHome device                         │
│  (panaac_v2 custom component)           │
└─────────────────────────────────────────┘
```

## MQTT topic contract

The integration expects the same topic layout as the ESPHome component:

| Topic suffix | Direction | Retained | Purpose |
|--------------|-----------|----------|---------|
| `availability` | device → HA | yes | `online` / `offline` |
| `traits` | device → HA | yes | capabilities JSON |
| `state` | device → HA | yes | current state JSON |
| `set` | HA → device | no | partial command JSON |

Default topic prefix: `panaac_v2/esphome-panaac-v2`.

### Traits payload

```json
{
  "hvac_modes": ["off", "cool", "heat", "fan_only", "dry", "auto"],
  "fan_modes": ["Auto", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Quiet", "Powerful"],
  "swing_modes": ["Auto", "Highest", "High", "Middle", "Low", "Lowest"],
  "swing_horizontal_modes": ["Auto", "Left Max", "Left", "Middle", "Right", "Right Max"],
  "min_temp": 16,
  "max_temp": 30,
  "temp_step": 0.5,
  "temperature_unit": "C"
}
```

The climate entity updates `_attr_*` from traits so the card reflects the
device’s actual capabilities.

### State payload

```json
{
  "mode": "cool",
  "target_temperature": 24.0,
  "fan_mode": "Level 2",
  "swing_mode": "Middle",
  "swing_horizontal_mode": "Right",
  "current_temperature": 26.5,
  "available": true
}
```

### Command payload

Partial JSON with any subset of the state keys:

```json
{"fan_mode": "Level 2"}
```

## ClimateEntity features

`PanaACV2Climate` is a pure push entity (`_attr_should_poll = False`): every
state update arrives over MQTT, so Home Assistant does not schedule poll cycles.

It reports:

- `TARGET_TEMPERATURE`
- `FAN_MODE` (only when `fan_modes` is non-empty)
- `SWING_MODE` (only when `swing_modes` is non-empty)
- `SWING_HORIZONTAL_MODE` (only when `swing_horizontal_modes` is non-empty)
- `TURN_ON` / `TURN_OFF` (only when more than one HVAC mode is advertised and
  `OFF` is among them)

Feature flags are computed dynamically in `supported_features` so each control
only appears once the device advertises the corresponding capability. Until the
first retained `traits` payload arrives the entity starts with conservative
capabilities — only `OFF` as an HVAC mode and empty fan/swing mode lists — so a
cold start or a lost retained-traits message cannot expose controls the device
does not support. The full control set is applied when `_handle_traits` fills
the mode lists.

## `hvac_action` (derived)

The entity exposes `hvac_action` so the climate building-block automation
surface works: the `started_cooling` / `started_drying` / `started_heating`
triggers and the `is_cooling` / `is_drying` / `is_heating` conditions all read
this attribute (see HA core `components/climate/trigger.py` and
`condition.py`).

The PanaAC controller is a **one-way IR transmitter**: it knows the mode it last
commanded (and the room/setpoint temperatures the unit reports) but not whether
the compressor is actually running. So `hvac_action` is *derived* on the Home
Assistant side in `_derive_hvac_action`, not reported verbatim by the device:

| Commanded mode | `hvac_action` |
|----------------|---------------|
| `off`          | `off`         |
| `cool`         | `cooling`     |
| `heat`         | `heating`     |
| `dry`          | `drying`      |
| `fan_only`     | `fan`         |
| `auto`         | `cooling` if room > setpoint, `heating` if room < setpoint, else `idle` |

The `auto` mapping is an inference from `current_temperature` vs.
`target_temperature` — the same comparison the AC's own thermostat makes — and
falls back to `idle` when the temperatures are unknown or the room is at
setpoint. This means `started_cooling` can fire while the compressor is in fact
coasting; that is the accepted trade-off of a one-way controller with no
power/run feedback. The derivation lives in the HA integration (consistent with
`_hvac_mode_from_str`, which also maps a raw payload string to a HA enum) so no
firmware reflash is required. If a power-usage sensor is ever added to the ESP
device, this should move to a real reported action.

## Config flow

The integration has a single config flow step asking for a `device_name` and the
`topic_prefix`. The topic prefix is used as the unique id, preventing duplicate
entries for the same device.

## Availability

`_attr_available` is set from the `availability` topic (`online` → True,
`offline` or missing → False). The entity starts as unavailable and becomes
available once the device publishes `online`.

## Error handling

- Invalid JSON payloads are logged and ignored.
- If a trait key is missing, the entity keeps its default value.
- If the MQTT integration is not loaded, the integration fails setup because
  `manifest.json` declares `"dependencies": ["mqtt"]`.

## Future extensions

- Auto-discovery from the ESPHome device’s retained `traits` topic, removing the
  need for a config flow.
- Additional sensor entities for `current_temperature` or power consumption.
- Service calls for IR-specific functions like "send physical remote command".

## Open items / not yet supported

- **Target humidity (open).** The Panasonic AC has no humidity setpoint — its
  `dry` mode is an HVAC mode, not a target humidity — so the entity does not
  expose `ClimateEntityFeature.TARGET_HUMIDITY`, `_attr_target_humidity`, or
  `async_set_humidity`, and the climate `target_humidity_changed` /
  `target_humidity_crossed_threshold` triggers, the `target_humidity`
  condition, and the `climate.set_humidity` service are not supported. If a
  humidity setpoint ever becomes meaningful (for example a separate
  dehumidifier or a humidity-sensor-driven automation), add the feature flag,
  the attribute, the setter, and a `target_humidity` field in the state payload.
- **Preset mode.** The entity exposes `Normal`, `Powerful`, and `Eco` when the device
  advertises those capabilities. `Powerful` and `Eco` map to the Panasonic IR preset bits;
  `Normal` clears the active preset. Presets are accepted only in Auto, Cool, and Dry modes.
