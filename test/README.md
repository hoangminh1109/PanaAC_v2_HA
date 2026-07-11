# PanaAC v2 HA — test plan

Full-test plan for the `panaac_v2` Home Assistant custom integration. This
branch (`testing/full-test`) holds the plan only — the integration source is
unchanged from `main`. A later agent (human or AI) executes the plan against a
running HA instance and the flashed ESPHome device.

The plan is split into two documents:

- [`test-specification.md`](test-specification.md) — **what** to test: the
  groups, inputs, expected behaviour, pass/fail criteria.
- [`test-execution.md`](test-execution.md) — **how** to run it: prerequisites,
  commands (incl. bringing up HA and reading the recorder DB without the owner
  password), example automation YAML, and how to read results.

## Scope (three groups)

1. **Traits consistency with the ESPHome configuration** — the HA climate
   entity's advertised modes/ranges match the device's `traits` payload for
   each ESPHome config variant, including the conservative defaults before the
   first `traits` message arrives.
2. **Two-way MQTT with the ESPHome side** — the integration subscribes to
   `availability`/`traits`/`state` and publishes commands to `set`; the device
   applies them and the HA entity reflects state; `hvac_action` is derived.
3. **Automation** — the climate automation surface end-to-end: the ESPHome-side
   `climate.control` action, lambda `make_call`, and `on_state`/`on_control`
   triggers (the device's changes must surface in HA), plus the HA-side
   climate building-block triggers/conditions/actions (`started_cooling`,
   `is_cooling`, `climate.set_hvac_mode`, …).

## Entity under test

- Climate entity `climate.test_ac_remote_controller_v2` (config entry
  "test ac", topic prefix `panaac_v2/esphome-panaac-v2`).
- HA core dev instance ≥ 2026.7 (the `new_triggers_conditions` labs flag is NOT
  required on 2026.7 stable and later).

## Status

Not yet executed. After execution, record results inline in
`test-execution.md` and commit to this branch.