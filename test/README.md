# PanaAC v2 HA — test plan

Full-test plan for the `panaac_v2` Home Assistant custom integration. These
documents describe how to validate the integration against a running HA
instance and the flashed ESPHome device.

Workspace layout is assumed to be relative to a portable `HA/` root:

```text
HA/
  ha/
    core/
    PanaAC_v2_HA/
  esphome/
    PanaAC_v2_ESPHome/
```

The plan is split into two documents:

- [`test-specification.md`](test-specification.md) — **what** to test: the
  groups, inputs, expected behaviour, pass/fail criteria.
- [`test-execution.md`](test-execution.md) — **how** to run it: prerequisites,
  commands (including a dev-environment-only validation path, HIL environment prep, bringing up HA,
  and reading the recorder DB without the owner password), example automation
  YAML, and how to read results.
- [`run_full_test.py`](run_full_test.py) — entrypoint for the automation
  runner.
- [`run_stubbed_pytest.py`](run_stubbed_pytest.py) — offline/stubbed pytest
  entrypoint for HA-side climate entity behavior.
- [`automation_runner/data.py`](automation_runner/data.py) — static test data
  and suite definitions.
- [`automation_runner/core.py`](automation_runner/core.py) — test framework,
  environment management, report generation, and suite execution.
- [`automation_runner/cli.py`](automation_runner/cli.py) — CLI and interactive
  menu for selecting suites and preparing the environment.
- [`pytest_stubbed/data.py`](pytest_stubbed/data.py) — stubbed test vectors for
  MQTT state/traits/command coverage.
- [`pytest_stubbed/test_climate_entity.py`](pytest_stubbed/test_climate_entity.py)
  — direct pytest coverage for subscriptions, state/traits ingestion,
  command publishing, derived `hvac_action`, and invalid-payload handling.

## Scope (three groups)

1. **Traits consistency with the live C3 firmware** — the HA climate entity's
   advertised modes/ranges match the device's `traits` payload on the flashed
   C3 firmware, including the conservative defaults before the first `traits`
   message arrives. The ESPHome variant matrix is covered in the ESPHome repo.
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

## Runner config

Create `test/runner_config.json` for local MQTT settings. Start from `test/runner_config.example.json`:

```json
{
  "mqtt": {
    "broker_mode": "external",
    "host": "127.0.0.1",
    "port": 1883,
    "user": "mqtt_user",
    "pass": "mqtt_pass"
  }
}
```

The real `test/runner_config.json` is ignored by git. Interactive menu flows will save prompted MQTT credentials there automatically. Set `broker_mode` to `external` when you want to reuse an existing broker.

`fresh-env` creates an isolated HA profile under `test/test_env/ha_config_<run-id>`, starts HA on port `8125`, onboards a local `tester` user, starts an isolated MQTT broker on a random localhost port, clears retained test topics under the runner topic prefix, seeds an MQTT config entry, and adds the `panaac_v2` config entry automatically.

## Runner usage

- `python3 test/run_full_test.py list`
- `python3 test/run_full_test.py dev-env`
- `python3 test/run_full_test.py setup-env`
- `python3 test/run_full_test.py fresh-env`
- `python3 test/run_full_test.py run --suite ha.g1 --suite ha.g2`
- `python3 test/run_full_test.py stubbed --group all`

By default, `dev-env`, `setup-env`, and `run` use a brand-new isolated HA profile instead of `ha/core/config` whenever you do not pass `--ha-config-path`. `run` deletes that profile during teardown, and `setup-env`/`dev-env` also clean up after themselves. Runs that do not include `ha.g3` default to a spawned isolated MQTT broker on a random localhost port, and setup paths clear retained test topics before seeding baseline state. Use `--keep-test-config` if you need to inspect the generated HA config after a failure.
- `python3 test/run_full_test.py menu`
- `python3 test/run_stubbed_pytest.py --group all`
- `python3 test/run_stubbed_pytest.py --group state`
- `python3 test/run_stubbed_pytest.py --group commands`
