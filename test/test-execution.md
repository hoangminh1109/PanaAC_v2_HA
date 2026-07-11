# PanaAC v2 HA — test execution instructions

How to run the tests in `test-specification.md`. Record each step's outcome on
its `Result:` line and commit this file to the `testing/full-test` branch.

## Prerequisites

- HA core dev instance ≥ 2026.7 running at `http://localhost:8123`, from
  `ha/core` with the workspace `core/.venv` (Python 3.14, `uv`). Bring it up
  with the `ha-dev-setup` skill, or:
  ```
  cd /home/hoangminh/AgentsWork/Codex/HA/ha/core
  echo 'mnhmnh' | sudo -S systemctl start mosquitto   # broker
  nohup ./.venv/bin/hass -c config > /tmp/ha_core_run.log 2>&1 &
  ```
- The `panaac_v2` integration is symlinked into
  `ha/core/config/custom_components/panaac_v2` and a config entry "test ac"
  (topic prefix `panaac_v2/esphome-panaac-v2`) exists. The MQTT integration is
  configured (`127.0.0.1:1883`, `mqtt_user`/`mqtt_pass`).
- **No labs flag needed** on HA ≥ 2026.7 (the climate building-block triggers
  are GA). If you are on an older dev build, see the DESIGN.md note; here we
  assume 2026.7+.
- The DUT is flashed with the `panaac_v2` v2 firmware (variant C3) and is
  online on the broker. Flashing instructions are in the ESPHome repo's
  `test/test-execution.md` ("Flashing the device"). For these HA tests the
  device only needs to be online and publishing retained `availability`/
  `traits`/`state`.
- mosquitto CLI for driving/observing: `mosquitto_pub`/`mosquitto_sub`.

Commands run from `ha/core` unless noted.

## Reading the entity state without the owner password

The HA owner password is not available to the test agent. Read the entity's
live attributes from the recorder SQLite DB instead (auth-free):

```
.venv/bin/python - <<'PY'
import sqlite3, json, time
con=sqlite3.connect("config/home-assistant_v2.db"); con.row_factory=sqlite3.Row
r=con.execute("""select s.state, sa.shared_attrs, s.last_updated_ts
from states s join states_meta m on s.metadata_id=m.metadata_id
left join state_attributes sa on s.attributes_id=sa.attributes_id
where m.entity_id='climate.test_ac_remote_controller_v2'
order by s.last_updated_ts desc limit 1""").fetchone()
att=json.loads(r["shared_attrs"]) if r and r["shared_attrs"] else {}
print("state=",r["state"],"hvac_action=",att.get("hvac_action"),
      "hvac_modes=",att.get("hvac_modes"),"fan_modes=",att.get("fan_modes"),
      "min=",att.get("min_temp"),"max=",att.get("max_temp"),
      "step=",att.get("target_temperature_step"),"@",
      time.strftime("%H:%M:%S",time.localtime(r["last_updated_ts"])) if r else "")
PY
```
The recorder commits a few seconds after a state change; poll until a new
`last_updated_ts` appears. To inspect a specific mode's `hvac_action`, publish
the state, wait ~2–3 s, then run the snippet.

## Group 1 — Traits consistency

### 1.1 Before first traits
Delete retained `traits` and restart HA so the entity starts cold:
```
mosquitto_pub -h 127.0.0.1 -u mqtt_user -P mqtt_pass -t panaac_v2/esphome-panaac-v2/traits -n -r
# restart HA (kill the hass python process, relaunch), wait for "Home Assistant initialized"
```
Read the entity (snippet above) before the device republishes `traits`. Expect
`hvac_modes=[off]`, empty fan/swing, only `TARGET_TEMPERATURE`. Result: …

### 1.2 Per variant
For each ESPHome variant C1–C6 (flash per the ESPHome repo's execution doc),
wait for the retained `traits`, and read the entity attributes. Compare to
`test-specification.md` §1.2. Capture `hvac_modes`, `fan_modes`, `swing_modes`,
`swing_horizontal_modes`, `min_temp`, `max_temp`, `target_temperature_step`,
and the `supported_features`-derived presence of fan/swing/turn_on-off.
Result C1: … C2: … C3: … C4: … C5: … C6: …

## Group 2 — Two-way MQTT with the ESPHome side

Observe the DUT topics in one terminal:
```
mosquitto_sub -h 127.0.0.1 -u mqtt_user -P mqtt_pass -t 'panaac_v2/esphome-panaac-v2/#' -v
```

### 2.1 Device → HA
Toggle the device availability (power-cycle the DUT or publish `online`/
`offline` to `.../availability` retained) and read the entity's availability
(via DB or the subscribe terminal). Confirm `state` updates map to entity
attributes. Result: …

### 2.2 HA → device round-trip
For each row in §2.2, publish the equivalent command directly (to isolate the
integration's `set` payload) OR call the HA service. Calling via the
integration: there is no owner token, so drive the **service through the MQTT
`set` path the integration uses** — i.e. publish the command and confirm the
integration republishes it on `<p>/set`. Simpler: assert the integration's
`async_set_*` produces the right `<p>/set` payload by watching the subscribe
terminal while invoking the service through an HA automation that calls the
action (see Group 3.4), or by reading `climate.py`'s `_publish_command`. To
keep it driver-free, publish the expected `<p>/set` payload directly and
confirm the DUT applies + the HA entity reflects. Result: …

### 2.3 Derived hvac_action
For each mode in the §2.3 table, publish a `state` payload:
```
mosquitto_pub -h 127.0.0.1 -u mqtt_user -P mqtt_pass \
  -t panaac_v2/esphome-panaac-v2/state \
  -m '{"mode":"cool","target_temperature":24,"fan_mode":"Auto","swing_mode":"Auto","current_temperature":27,"available":true}'
```
wait ~3 s, read `hvac_action` from the DB, assert it matches. For `auto`, vary
`current_temperature` (28/20/24) vs `target_temperature` 24. Result: …

### 2.4 Availability & retained resilience
Stop mosquitto (`sudo systemctl stop mosquitto`), confirm the HA entity goes
unavailable; restart it, confirm recovery after the device republishes. Delete
retained `traits` and restart HA; confirm the conservative defaults (§1.1)
then recovery when the device republishes. Result: …

## Group 3 — Automation

Create a temporary test automation file at
`ha/core/config/automations.yaml` (back up the existing `[]` first), then
restart HA to load it. Use the current docs style:

```yaml
- id: panaac_test_started_cooling
  alias: panaac test started_cooling
  mode: single
  triggers:
    - trigger: climate.started_cooling
      target:
        entity_id: climate.test_ac_remote_controller_v2
  actions:
    - action: mqtt.publish
      data:
        topic: panaac_v2/test/started_cooling
        payload: "fired"
- id: panaac_test_is_cooling
  alias: panaac test is_cooling
  mode: single
  triggers:
    - trigger: state
      entity_id: climate.test_ac_remote_controller_v2
      attribute: hvac_action
      to: cooling
  conditions:
    - condition: climate.is_cooling
      target:
        entity_id: climate.test_ac_remote_controller_v2
  actions:
    - action: mqtt.publish
      data:
        topic: panaac_v2/test/is_cooling
        payload: "fired"
```

Subscribe to the test topics, then drive `off → cool`:
```
mosquitto_sub -h 127.0.0.1 -u mqtt_user -P mqtt_pass -t 'panaac_v2/test/#' -v &
mosquitto_pub ... -t panaac_v2/esphome-panaac-v2/state -m '{"mode":"off",...}'
sleep 1.5
mosquitto_pub ... -t panaac_v2/esphome-panaac-v2/state -m '{"mode":"cool",...}'
```
### 3.4 result: expect `panaac_v2/test/started_cooling fired` and
`panaac_v2/test/is_cooling fired`. Repeat analogously for `started_heating` /
`started_drying` and the `is_heating` / `is_drying` conditions. Result: …

### 3.1 / 3.2 ESPHome climate.control / lambda observed via HA
On the DUT, trigger `climate.control` and a lambda `make_call().perform()` (per
the ESPHome repo's test YAML, e.g. the "Control cool 24C" / a lambda button),
changing mode/temp/fan/swing. After each, read the HA entity (DB) and assert it
reflects the DUT's new `state`. Result: …

### 3.3 on_state / on_control
Confirm an HA command (`climate.set_*` via the integration, or the `off → cool`
publish above) causes the DUT to apply + republish (new recorder `states` row
for the entity = `on_state` reaching HA). To confirm `on_control` specifically,
watch the DUT log (DEBUG) for the `on_control`/`on_state` log lines added by
the ESPHome test YAML. Result: …

## Finishing

- Restore `ha/core/config/automations.yaml` to `[]` and restart HA to leave the
  instance clean. Remove any retained `panaac_v2/test/*` messages:
  `mosquitto_pub ... -t panaac_v2/test/started_cooling -n -r` (etc.).
- Record every `Result:` line. Commit to the `testing/full-test` branch. Do not
  push unless asked.
