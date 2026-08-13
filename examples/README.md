# Examples

Working automations and scripts built on this integration, kept here rather than
in the README because they are longer than a snippet and depend on each other.

They use the entity IDs from the setup they were written on
(`climate.s_b_volcano_h`, `switch.s_b_volcano_h_fan`, …). Yours will differ —
substitute your own before using them.

| File | What it does |
| --- | --- |
| [`script/sesh.yaml`](script/sesh.yaml) | A full session: heat up, wait for the bag to fill, wait for it to empty, step the target temperature to the next value in an `input_select`, then switch off. |
| [`automation/volcano_auto_sesh.yaml`](automation/volcano_auto_sesh.yaml) | Starts that script automatically when the heater comes on, and flashes the screen as an acknowledgement. |
| [`custom_templates/utilities.jinja`](custom_templates/utilities.jinja) | `find_next_temp` (the next value above the current target) and `get_options` (read an `input_select`'s options). Required by `sesh.yaml`. |
| [`custom_templates/createExecutionId.jinja`](custom_templates/createExecutionId.jinja) | Generates a run id, used for correlating log lines from one session. |

The `.jinja` files go in `<config>/custom_templates/` and need a
`homeassistant.reload_custom_templates` call — or a restart — before they are
importable.

Shorter, self-contained examples are in the
[Use cases](../README.md#use-cases) section of the main README.
