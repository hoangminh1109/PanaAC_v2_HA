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
| `state` | device → HA | no | current state JSON |
| `set` | HA → device | no | partial command JSON |

Default topic prefix: `panaac_v2/esphome-panaac-v2`.

### Traits payload

```json
{
  "hvac_modes": ["off", "cool", "heat", "fan_only", "dry", "auto"],
  "fan_modes": ["Auto", "Level 1", "Level 2", "Level 3", "Level 4", "Level 5", "Quiet"],
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

`PanaACV2Climate` reports:

- `TARGET_TEMPERATURE`
- `FAN_MODE`
- `SWING_MODE`
- `SWING_HORIZONTAL_MODE` (only when `swing_horizontal_modes` is non-empty)

Feature flags are computed dynamically in `supported_features` so the horizontal
swing dropdown only appears when the device advertises horizontal-swing support.

## Config flow

The integration has a single config flow step asking for `topic_prefix`.
The topic prefix is used as the unique id, preventing duplicate entries for the
same device.

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
