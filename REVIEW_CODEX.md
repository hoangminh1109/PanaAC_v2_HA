# Codex Review

## Re-review update (2026-07-11)

Reviewed `origin/bugfix/fix-codex-findings`.

Result: all three findings below appear fixed in the branch revision I reviewed. I did not identify a remaining correctness issue in the updated implementation or the synchronized documentation.

## ~~Issue 1: the entity is still configured as a polling entity~~

Status: fixed on `bugfix/fix-codex-findings`.

**Problem**

This integration is entirely MQTT push-driven, but the climate entity never disables polling. That causes Home Assistant to treat it like a poll-based entity and schedule unnecessary update cycles.

**Technical root cause**

`PanaACV2Climate` subscribes to MQTT topics and updates itself from callbacks, but it never sets `_attr_should_poll = False`. In Home Assistant, `Entity` defaults to polling unless a subclass explicitly opts out.

Relevant code:

- `custom_components/panaac_v2/climate.py:71`

**Proposed fix**

Set `_attr_should_poll = False` on the entity class so Home Assistant treats it as a pure push entity.

## ~~Issue 2: unsupported capabilities are advertised before the first `traits` payload arrives~~

Status: fixed on `bugfix/fix-codex-findings`.

**Problem**

The integration boots with optimistic defaults for HVAC modes, fan modes, swing modes, and horizontal swing support. Until the retained `traits` message arrives, the UI can expose controls that the actual device does not support. On a cold start or after retained-topic loss, that can leave the entity presenting an incorrect control surface.

**Technical root cause**

The class-level defaults hard-code the full capabilities set, including horizontal swing, and `supported_features` is computed directly from those defaults. The integration does not start from a conservative "unknown capabilities" state.

Relevant code:

- `custom_components/panaac_v2/climate.py:74`
- `custom_components/panaac_v2/climate.py:113`
- `custom_components/panaac_v2/climate.py:217`

**Proposed fix**

Initialize the entity with conservative capabilities, ideally just the minimal shape needed to render safely, and only expose the full control set after a valid `traits` payload has been received. In practice that means starting with empty or minimal mode lists and no horizontal swing feature.

## ~~Issue 3: the repository documentation is stale and internally inconsistent~~

Status: fixed on `bugfix/fix-codex-findings`.

**Problem**

The HA repo still tells the user to install "Panasonic AC v3 (MQTT)" even though this repository is `panaac_v2`, and its MQTT contract docs disagree with the actual ESPHome implementation on whether `state` is retained. These mismatches will create avoidable setup and troubleshooting errors.

**Technical root cause**

The documentation appears to have been copied forward from an earlier repo generation and was not fully reconciled with the final v2 naming and topic behavior.

Relevant docs:

- `README.md:39`
- `README.md:42`
- `README.md:54`
- `INSTALL.md:32`
- `INSTALL.md:53`
- `DESIGN.md:48`

**Proposed fix**

Update all installation and design docs to use the actual v2 integration name consistently, and align the MQTT topic contract with the ESPHome repo's implemented behavior. The docs should describe one authoritative state-retention model, not two conflicting ones.
