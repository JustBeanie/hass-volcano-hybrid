# Roadmap

What is planned next, grouped by [milestone](https://github.com/JustBeanie/hass-volcano-hybrid/milestones).
Each item is an issue, and the issue is where the detail and the progress live.
This page only lists them.

## [Portables out of experimental](https://github.com/JustBeanie/hass-volcano-hybrid/milestone/1)

The Crafty, Crafty+, Venty and Veazy are implemented from Storz & Bickel's own web
app, by way of SavageNL's protocol notes, and have never run on real hardware. This
milestone is done when each one is confirmed on a real device and the README's
support matrix says so.

| Issue | What |
| --- | --- |
| [#24](https://github.com/JustBeanie/hass-volcano-hybrid/issues/24) | Crafty / Crafty+: confirm on real hardware |
| [#25](https://github.com/JustBeanie/hass-volcano-hybrid/issues/25) | Venty: confirm on real hardware |
| [#26](https://github.com/JustBeanie/hass-volcano-hybrid/issues/26) | Veazy: confirm on real hardware |
| [#27](https://github.com/JustBeanie/hass-volcano-hybrid/issues/27) | A *Device report* issue form for hardware diagnostics |
| [#28](https://github.com/JustBeanie/hass-volcano-hybrid/issues/28) | Confirm sleep and wake behavior on a real portable |
| [#29](https://github.com/JustBeanie/hass-volcano-hybrid/issues/29) | Take the confirmed devices out of experimental |

**Own one?** You can move this milestone along without writing any code. Open an
issue with the device's **diagnostics download** attached (device page → ⋮ →
Download diagnostics), plus the model and firmware version, whether it works or
not.

## [Home Assistant 2027 readiness](https://github.com/JustBeanie/hass-volcano-hybrid/milestone/2)

Home Assistant removes several APIs between 2027.8 and 2027.11. 4.1.0 already moved
off every one announced so far. This milestone keeps it that way: a coming break
should show up the night a beta ships rather than in a release.

| Issue | What |
| --- | --- |
| [#30](https://github.com/JustBeanie/hass-volcano-hybrid/issues/30) | Make a failing prerelease test run visible |
| [#31](https://github.com/JustBeanie/hass-volcano-hybrid/issues/31) | Fail the prerelease test run on deprecation log lines |
| [#32](https://github.com/JustBeanie/hass-volcano-hybrid/issues/32) | Check each 2027.x beta for deprecations |
| [#33](https://github.com/JustBeanie/hass-volcano-hybrid/issues/33) | Write down the minimum Home Assistant version policy |
| [#34](https://github.com/JustBeanie/hass-volcano-hybrid/issues/34) | Check the `bleak-retry-connector` floor against Home Assistant 2027 |

## Not on the roadmap

The README's [Known limitations](README.md#known-limitations) are constraints of the
devices themselves, not planned work. The Mighty and Mighty+ are not supported
because no Bluetooth protocol for them is known.
