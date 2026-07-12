# PanaAC v2 HA — test specification

What the tests check. See `test-execution.md` for how to run them. Integration
reference: the active repo branch under test.

## Conventions

- DUT = the ESP8266 running the `panaac_v2` firmware (v2 MQTT mode).
- Broker = local mosquitto (`127.0.0.1:1883`, `mqtt_user`/`mqtt_pass`).
- Topic prefix `<p>` = `panaac_v2/esphome-panaac-v2`.
- Entity = `climate.test_ac_remote_controller_v2`.
- Pass = every asserted expectation holds; Fail = record actual vs expected.
- Reading the entity's live state without the HA owner password: query the
  recorder SQLite DB (`core/config/home-assistant_v2.db`, table `states`
  joined to `state_attributes`/`states_meta`) — see `test-execution.md`.

## Config items under test (HA side)

The integration exposes (from `climate.py`):
- HVAC modes: starts as `[off]`; set from `traits.hvac_modes`.
- Fan modes: starts `[]`; set from `traits.fan_modes`.
- Swing modes / swing horizontal modes: start `[]`; set from `traits`.
- `min_temp`/`max_temp`/`target_temperature_step`: defaults 16/30/0.5, set from
  `traits`.
- Features: `TARGET_TEMPERATURE` always; `FAN_MODE`/`SWING_MODE`/
  `SWING_HORIZONTAL_MODE` when the corresponding mode list is non-empty;
  `TURN_ON`/`TURN_OFF` when `len(hvac_modes) > 1 and off in hvac_modes`.
- `hvac_action`: derived in `_derive_hvac_action` from the mode (+ room vs
  setpoint for `auto`).
- Does NOT support: target humidity, preset mode (see `DESIGN.md` open items).

---

## Group 1 — Traits consistency with the live C3 firmware

**Goal:** the HA entity advertises exactly what the flashed C3 device's
`traits` payload says, and is conservative before `traits` arrives.

### 1.1 Before the first `traits` message
On a fresh start with no retained `traits` (delete the retained message first):
- `hvac_modes` == `["off"]` only.
- `fan_modes` / `swing_modes` / `swing_horizontal_modes` == `[]`.
- `supported_features` == `TARGET_TEMPERATURE` only (no TURN_ON/TURN_OFF).
- The entity is available only after `availability` = `online`.
Pass: the entity is safe/empty until `traits` arrives; no unsupported controls
are exposed.

### 1.2 After `traits`, C3 baseline
With the device flashed to C3, after the retained `traits` arrives, the HA
entity's attributes must equal the device payload:

- `hvac_modes` == `traits.hvac_modes` (order-independent).
- `fan_modes` == `traits.fan_modes`.
- `swing_modes` == `traits.swing_modes`;
  `swing_horizontal_modes` == `traits.swing_horizontal_modes` (empty when the
  ESPHome variant has `swing_horizontal=false`).
- `min_temp`/`max_temp`/`target_temperature_step` == `traits.min_temp`/
  `max_temp`/`temp_step`.
- `supported_features` includes `FAN_MODE` iff `fan_modes` non-empty,
  `SWING_MODE` iff `swing_modes` non-empty, `SWING_HORIZONTAL_MODE` iff
  `swing_horizontal_modes` non-empty, `TURN_ON`/`TURN_OFF` iff
  `len(hvac_modes) > 1 and "off" in hvac_modes`.

Example — C3 baseline (5-level, swing horizontal, all modes):
`hvac_modes=[off,cool,heat,fan_only,dry,auto]`,
`fan_modes=[Auto,Level 1,Level 2,Level 3,Level 4,Level 5,Quiet]`,
`swing_modes=[Auto,Highest,High,Middle,Low,Lowest]`,
`swing_horizontal_modes=[Auto,Left Max,Left,Middle,Right,Right Max]`,
`min_temp=16, max_temp=30, target_temperature_step=0.5`, all five features on.

Pass: §1.1 and §1.2 hold for the flashed C3 firmware.

---

## Group 2 — Two-way MQTT with the ESPHome side

**Goal:** the integration and the DUT exchange state/commands correctly and
`hvac_action` is derived. Run with variant **C3**.

### 2.1 Device → HA (subscribe)
- `availability` `online`/`offline` toggles the entity's availability.
- `traits` (retained) updates the entity's advertised modes (per Group 1).
- `state` updates `hvac_mode`, `current_temperature`, `target_temperature`,
  `fan_mode`, `swing_mode`, `swing_horizontal_mode`.

### 2.2 HA → device (command round-trip)
Call each HA climate service on the entity; the integration publishes a partial
JSON to `<p>/set`; the DUT applies it and republishes `state`; the HA entity
reflects the new value:

| HA action | data | Asserted `set` payload + effect |
|-----------|------|----------------------------------|
| `climate.set_hvac_mode` | `hvac_mode: cool` | `{"mode":"cool"}` |
| `climate.set_temperature` | `temperature: 24` | `{"target_temperature":24}` |
| `climate.set_temperature` | `temperature: 24, hvac_mode: cool` | `{"target_temperature":24,"mode":"cool"}` (one message) |
| `climate.set_fan_mode` | `fan_mode: "Level 2"` | `{"fan_mode":"Level 2"}` |
| `climate.set_swing_mode` | `swing_mode: Middle` | `{"swing_mode":"Middle"}` |
| `climate.set_swing_horizontal_mode` | `swing_horizontal_mode: Left` | `{"swing_horizontal_mode":"Left"}` |
| `climate.turn_on` / `turn_off` / `toggle` | — | `{"mode":"..."}` (only when the turn_on/off feature is advertised) |

Out-of-range temperature is clamped by the device (16–30).

### 2.3 Derived `hvac_action`
For each device `mode` (drive via `set`), assert the HA entity's `hvac_action`
attribute (read from the recorder DB or the entity state):

| mode (state payload) | expected `hvac_action` |
|----------------------|------------------------|
| `off` | `off` |
| `cool` | `cooling` |
| `heat` | `heating` |
| `dry` | `drying` |
| `fan_only` | `fan` |
| `auto`, current > target | `cooling` |
| `auto`, current < target | `heating` |
| `auto`, current == target (or unknown) | `idle` |

### 2.4 Availability & retained-message resilience
- Stop the broker (or delete the retained `state`/`traits`) and restart; the
  entity becomes unavailable then recovers when the device republishes.
- Delete the retained `traits`; restart HA; the entity is conservative (per
  §1.1) until the device republishes `traits` (next reconnect/loop).

Pass: §2.1–2.4 all hold; `hvac_action` matches the table for every mode.

---

## Group 3 — Automation

**Goal:** the climate automation surface works end-to-end. This covers the
ESPHome-side automation features (as observed through HA) AND the HA-side
climate building-block automations.

### 3.1 ESPHome `climate.control` action observed via HA
On the DUT, run `climate.control` (a template button or `on_boot`) changing
`mode`/`target_temperature`/`fan_mode`/`swing_mode`. The DUT republishes
`state`; assert the HA entity's `hvac_mode`/`target_temperature`/`fan_mode`/
`swing_mode` reflect the new values (read via recorder DB).

### 3.2 ESPHome lambda `make_call` observed via HA
On the DUT, a lambda `make_call().perform()` that changes a field; assert the
HA entity reflects it (same as 3.1).

### 3.3 ESPHome `on_state` / `on_control` triggers
- `on_state` fires on every state publish; from HA this is observable as a
  new recorder `states` row for the entity whenever the DUT republishes.
- `on_control` fires when HA sends a command (`climate.set_*` → `<p>/set`):
  the DUT applies it (control call) and republishes (`on_state`). Assert that
  an HA `climate.set_hvac_mode` results in the device applying the mode and the
  HA entity reflecting it (control round-trip of §2.2), i.e. the device's
  `on_control` → `on_state` path is exercised.

### 3.4 HA-side climate building-block triggers/conditions/actions
Using the current HA YAML style (`triggers:`/`conditions:`/`actions:` with
`trigger:`/`condition:`/`action:` keys), assert:

- **Trigger** `climate.started_cooling` fires on the `off → cool` transition
  (entity `hvac_action` off→cooling).
- **Trigger** `climate.started_heating` / `climate.started_drying` fire on the
  analogous transitions.
- **Condition** `climate.is_cooling` passes when `hvac_action == cooling` and
  fails otherwise; same for `is_heating` / `is_drying`.
- **Condition** `climate.is_hvac_mode` passes for the matching mode.
- **Action** `climate.set_hvac_mode` / `set_temperature` / `set_fan_mode` /
  `set_swing_mode` / `set_swing_horizontal_mode` change the entity and command
  the DUT (round-trip of §2.2).
- **Action** `climate.turn_on` / `turn_off` / `toggle` are available only when
  the turn_on/off feature is advertised (more than one mode incl. `off`).

Pass: §3.1–3.4 all hold; the building-block trigger/condition automations fire
/ evaluate as specified with **no labs flag** (HA ≥ 2026.7).
