# Installation guide — PanaAC v2 (Home Assistant)

## What you need

- A working Home Assistant instance.
- The MQTT integration configured in Home Assistant and connected to the same
  broker used by the ESPHome device.
- The ESPHome device flashed with `PanaAC_v2_ESPHome` and connected to the MQTT
  broker.

## MQTT broker setup

If you have not already configured MQTT in Home Assistant:

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **MQTT** and select it.
3. Enter your broker hostname/IP, port, username and password.
4. For local/test brokers that do not support MQTT 5, set the protocol to
   **3.1.1** in the integration options.

Verify the broker is connected: the MQTT integration card should show
"X devices and Y entities" or a green indicator.

## Install the custom integration

### Method 1: HACS (recommended)

1. Ensure [HACS](https://hacs.xyz/) is installed.
2. Open HACS and go to **Integrations**.
3. Click the menu (⋮) and select **Custom repositories**.
4. Add `https://github.com/hoangminh1109/PanaAC_v2_HA` with category **Integration**.
5. Install **Panasonic AC v3 (MQTT)**.
6. Restart Home Assistant.

### Method 2: Copy the folder

```bash
# On the Home Assistant host (or container), from this repo:
cp -r custom_components/panaac_v2 /config/custom_components/
```

### Method 3: Symlink (good for development)

```bash
ln -s /path/to/PanaAC_v2_HA/custom_components/panaac_v2 /config/custom_components/panaac_v2
```

After copying, symlinking, or installing through HACS, **restart Home Assistant**.

## Add the integration

1. In Home Assistant go to **Settings → Devices & Services → Add Integration**.
2. Search for **Panasonic AC v3 (MQTT)**.
3. Enter the MQTT topic prefix configured in the ESPHome device YAML. The
   default is:
   ```
   panaac_v2/esphome-panaac-v2
   ```
4. Submit.

A new device with one climate entity should appear.

## Verify

1. The device card should show:
   - HVAC modes: `Off`, `Cool`, `Heat`, `Fan only`, `Dry`, `Auto`
   - Fan modes: `Auto`, `Level 1` … `Level 5`, `Quiet`
   - Swing modes: `Auto`, `Highest`, `High`, `Middle`, `Low`, `Lowest`
   - Horizontal swing modes: `Auto`, `Left Max`, `Left`, `Middle`, `Right`, `Right Max`
2. Change fan mode to `Level 2` on the climate card. The ESPHome device should:
   - receive the command on `panaac_v2/esphome-panaac-v2/set`,
   - transmit the matching IR packet,
   - publish the updated state on `panaac_v2/esphome-panaac-v2/state`.
3. The climate card should reflect the new state.

You can watch MQTT traffic with:

```bash
mosquitto_sub -h YOUR_BROKER -t 'panaac_v2/esphome-panaac-v2/#' -v
```

## Files

```
custom_components/panaac_v2/
  __init__.py      — entry setup
  climate.py       — ClimateEntity implementation
  config_flow.py   — configuration flow
  const.py         — constants
  manifest.json    — integration metadata
  strings.json     — UI strings
  translations/en.json
```

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Integration is not listed | The `custom_components/panaac_v2/` folder is not in the right place, or HA has not restarted. |
| Entity is unavailable | MQTT broker not connected, or topic prefix mismatch, or the ESPHome device is offline. |
| Traits do not match device | The device has not yet published retained `traits`, or the topic prefix is wrong. |
| Commands do nothing | MQTT broker is read-only for the HA user, or the ESPHome device is not subscribed to `.../set`. |
| Horizontal swing dropdown missing | The device did not advertise `swing_horizontal_modes` in traits, or `swing_horizontal` is false in the ESPHome YAML. |

## Next step

If you have not already flashed the ESPHome device, follow the installation
guide at https://github.com/hoangminh1109/PanaAC_v2_ESPHome.
