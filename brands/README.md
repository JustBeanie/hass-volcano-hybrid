# Brand assets

For the [`brands`](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/brands/)
quality-scale rule, these need to be submitted as a pull request to
[home-assistant/brands](https://github.com/home-assistant/brands) under
`custom_integrations/volcano_hybrid/`. They do nothing sitting in this repository.

## Status: not ready to submit

`custom_integrations/volcano_hybrid/logo.png` and `logo@2x.png` are generated from
the only source available — a 1440x810 **JPEG** that was previously committed here
with a `.png` extension. They are now real PNGs, trimmed to the artwork and sized
inside the spec (572x213 and 1144x426, shortest side within the required ranges).

Two things still block a submission:

1. **No icon.** `icon.png` (256x256) and `icon@2x.png` (512x512) must be square. The
   artwork is a 2.69:1 wordmark with no square element that reads on its own. The
   script `V` is the only candidate and cropping it invents a mark the brand owner
   never made.
2. **Quality.** The source is JPEG-compressed, so there are ringing artefacts around
   the lettering, and the light grey panel is baked into the image rather than being
   transparent. The spec prefers transparency. The panel cannot simply be keyed out
   because the word "HYBRID" is white and would vanish against a light theme.

The fix is a clean source asset — ideally vector, or a PNG with a real alpha channel
— from Storz & Bickel's press material, rather than anything derived further from
this JPEG.

## Regenerating

The current files were produced by trimming the source to its artwork bounding box,
saving that at native resolution as `logo@2x.png`, and downscaling by half with
Lanczos for `logo.png` — so neither is upscaled.
