---
name: Bug report
about: Something is not working
title: ''
labels: bug
assignees: ''

---

**What happened**

A clear description of the problem, and what you expected instead.

**Diagnostics**

Please attach a diagnostics dump — this is by far the most useful thing you can
include. Settings → Devices & services → Volcano Hybrid → the three-dot menu →
**Download diagnostics**.

It contains the raw bytes read from each Bluetooth characteristic, which is what
actually identifies a decoding problem. Your MAC address and serial number are
redacted, so it is safe to attach here.

**Logs**

Add this to `configuration.yaml`, restart, reproduce the problem, then paste the
relevant lines:

```yaml
logger:
  logs:
    custom_components.volcano_hybrid: debug
```

```
paste logs here
```

**Setup**

- Home Assistant version:
- Integration version:
- Volcano Hybrid firmware version: <!-- the "Firmware version" sensor on the device page -->
- How the vaporizer is reached: <!-- Bluetooth adapter on the HA host, or an ESPHome Bluetooth proxy -->
- Signal strength if known: <!-- the "rssi" field in the diagnostics dump -->

**Before you file**

- [ ] The Storz & Bickel phone app is not connected to the vaporizer — it only
      accepts one Bluetooth connection at a time
- [ ] The vaporizer appears under Settings → Devices & services → Bluetooth →
      three-dot menu → Advertisement monitor

**Anything else**
