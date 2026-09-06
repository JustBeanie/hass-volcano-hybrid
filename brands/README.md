# Brand assets

## Runtime branding

Home Assistant 2026.3 and newer loads the integration's runtime branding from
[`custom_components/volcano_hybrid/brand/`](../custom_components/volcano_hybrid/brand/).
Those files are the approved Volcano Hybrid HA × S&B artwork and are included
with the integration itself.

The files in this directory are retained only as a staging area for a future
pull request to the Home Assistant brands repository.

For the [`brands`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/brands/)
quality-scale rule, these need to be submitted as a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands) under
`custom_integrations/volcano_hybrid/`. They do nothing sitting in this repository.

## Status: not yet submitted

The staging directory now contains the same approved transparent artwork as the
runtime `brand/` directory:

- `icon.png` and `icon@2x.png`: the square volcano-and-house mark.
- `logo.png` and `logo@2x.png`: the full `HA × S&B` lockup.

The files meet the PNG, transparency and size requirements. The quality-scale item
remains `todo` only because the assets have not yet been submitted to the upstream
Home Assistant brands repository.

## Regenerating

The runtime icon was generated from the approved lockup, and the logo variants are
downscaled copies of the approved transparent lockup. The normal and hDPI files are
256x256 and 512x512 respectively.
