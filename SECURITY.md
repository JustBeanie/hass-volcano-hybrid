# Security policy

## Supported versions

Only the latest release gets fixes.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through
[GitHub's security advisories](https://github.com/JustBeanie/hass-volcano-hybrid/security/advisories/new),
with what you found and how to reproduce it.

## What this integration can reach

It talks to the vaporizer over Bluetooth LE on your own network and to nothing
else: no cloud service, no account and no telemetry. It can switch a heating
element on, so anything that lets someone drive the device without Home
Assistant's permission checks is in scope.
