# PanaAC v2 Home Assistant Integration

## Scope

This repository contains the MQTT-driven Home Assistant custom integration in
`custom_components/panaac_v2/`. Keep integration changes compatible with the
MQTT contract implemented by the sibling `PanaAC_v2_ESPHome` repository.

## Implementation Guidelines

- Keep entity behavior in `climate.py`; keep config-flow and constants changes
  narrow and consistent with `manifest.json`, `strings.json`, and translations.
- Accept and publish partial JSON commands on `<topic_prefix>/set`. Traits and
  state must remain conservative before retained MQTT traits arrive.
- Preserve the established PanaAC v2 names and semantics: HA uses the preset
  values `None`, `Powerful`, and `Eco`; native ESPHome Boost maps to HA
  `Powerful`. Powerful fan and preset behavior are coupled.
- Update `icons.json` whenever a user-facing preset or other state label gains
  a dedicated icon.
- Do not modify Home Assistant Core in `../../ha/core`; it is a test dependency,
  not part of this repository.

## Testing

The canonical tests live in the sibling `../PanaAC_v2_Testing` repository.
After integration behavior changes, run:

```bash
cd ../PanaAC_v2_Testing
python3 run_full_test.py stubbed --group all
python3 run_full_test.py run --suite ha.g1 --suite ha.g2
```

Run `ha.g3` only when a configured live DUT and external MQTT broker are in
scope. Add or update the matching fixture/regression test in
`../PanaAC_v2_Testing/fixtures/ha/pytest_stubbed/` for behavior changes.

## Repository Hygiene

- Do not commit credentials, MQTT passwords, HA databases, cache directories,
  generated files, or local configuration.
- Keep README/DESIGN/INSTALL documentation aligned with MQTT or user-visible
  changes.
- Use focused, imperative commits. Do not include unrelated workspace changes.
