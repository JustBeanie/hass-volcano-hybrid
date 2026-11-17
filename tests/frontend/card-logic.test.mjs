// What the dashboard card shows, tested without a browser: `node --test`.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CARD_TYPE,
  cardModel,
  deviceOf,
  devices,
  entitySuggestion,
  presets,
  progress,
  resolveDevice,
  statusItems,
  statusSupported,
  stepTarget,
  stubConfig,
  temperatureUnit,
} from "../../custom_components/storzandbickel_ble/frontend/card-logic.js";

const OURS = "storzandbickel_ble";

const entry = (entity_id, device_id, translation_key = null, platform = OURS) => ({
  entity_id,
  device_id,
  translation_key,
  platform,
});

const state = (entity_id, value, attributes = {}) => ({
  entity_id,
  state: value,
  attributes,
});

/** A `hass` holding one Volcano, renamed the way a real install renames it. */
function volcano(overrides = {}) {
  const entities = [
    entry("climate.s_b_volcano_h", "dev1"),
    entry("binary_sensor.living_room_ready", "dev1", "ready"),
    entry("binary_sensor.living_room_connection", "dev1", "connection"),
    entry("sensor.living_room_link_health", "dev1", "link_health"),
    entry("switch.s_b_volcano_h_fan", "dev1", "fan"),
    // Somebody else's climate entity must never be mistaken for ours.
    entry("climate.hallway", "dev9", null, "ecobee"),
  ];
  const states = [
    state("climate.s_b_volcano_h", "off", {
      hvac_action: "off",
      fan_modes: ["off", "on"],
    }),
    state("binary_sensor.living_room_ready", "off"),
    state("binary_sensor.living_room_connection", "on"),
    state("sensor.living_room_link_health", "connected"),
    ...(overrides.states ?? []),
  ];
  return hassOf([...entities, ...(overrides.entities ?? [])], states);
}

function hassOf(entities, states) {
  const byId = (list) => Object.fromEntries(list.map((item) => [item.entity_id, item]));
  return {
    entities: byId(entities),
    states: byId(states),
    localize: (key) =>
      ({
        [`component.${OURS}.entity.binary_sensor.ready.name`]: "Ready",
        [`component.${OURS}.entity.button.find.name`]: "Find device",
        "state.default.unavailable": "Unavailable",
      })[key] ?? "",
    formatEntityState: (stateObj) => `<${stateObj.state}>`,
    formatEntityAttributeValue: (stateObj, attribute) =>
      `<${stateObj.attributes[attribute]}>`,
  };
}

test("entities are found by what they are, not by their entity ID", () => {
  assert.deepEqual(resolveDevice(volcano(), "dev1"), {
    climate: "climate.s_b_volcano_h",
    ready: "binary_sensor.living_room_ready",
    connection: "binary_sensor.living_room_connection",
    linkHealth: "sensor.living_room_link_health",
  });
});

test("a device that is not ours, or has no climate entity, resolves to nothing", () => {
  const hass = volcano({
    entities: [entry("sensor.orphan_link_health", "dev2", "link_health")],
  });
  assert.equal(resolveDevice(hass, "dev9"), undefined);
  assert.equal(resolveDevice(hass, "dev2"), undefined);
  assert.equal(resolveDevice(hass, ""), undefined);
  assert.equal(resolveDevice({}, "dev1"), undefined);
});

test("only our devices with a climate entity are offered", () => {
  const hass = volcano({
    entities: [
      entry("climate.crafty", "dev0"),
      entry("sensor.orphan_link_health", "dev2", "link_health"),
    ],
  });
  assert.deepEqual(devices(hass), ["dev0", "dev1"]);
  assert.equal(deviceOf(hass, "climate.s_b_volcano_h"), "dev1");
  assert.equal(deviceOf(hass, "climate.hallway"), undefined);
  assert.equal(deviceOf(hass, "climate.nonexistent"), undefined);
});

test("temperatures are in Home Assistant's own unit", () => {
  assert.equal(temperatureUnit({ config: { unit_system: { temperature: "°F" } } }), "°F");
  assert.equal(temperatureUnit({ config: { unit_system: { temperature: "°C" } } }), "°C");
  assert.equal(temperatureUnit({}), "°C");
});

test("presets default to round numbers in the unit in use", () => {
  assert.deepEqual(presets({}, "°C", 40, 230), [175, 185, 195, 205]);
  assert.deepEqual(presets({}, "°F", 104, 446), [350, 365, 380, 400]);
  // A portable tops out lower, so a preset it cannot reach is not offered.
  assert.deepEqual(presets({}, "°C", 40, 190), [175, 185]);
});

test("a card's own presets are cleaned up, not trusted", () => {
  // The editor hands them over as text.
  assert.deepEqual(
    presets({ presets: ["190", "180", "abc", "180", "999", ""] }, "°C", 40, 230),
    [180, 190]
  );
  // Nothing usable falls back to the defaults rather than an empty row.
  assert.deepEqual(presets({ presets: ["x"] }, "°C", 40, 230), [175, 185, 195, 205]);
  assert.deepEqual(presets({ presets: "185" }, "°C", 40, 230), [175, 185, 195, 205]);
});

test("stepping the target stays inside what the device accepts", () => {
  assert.equal(stepTarget(185, 1, 40, 230), 186);
  assert.equal(stepTarget(185.4, -1, 40, 230), 184);
  assert.equal(stepTarget(230, 1, 40, 230), 230);
  assert.equal(stepTarget(40, -1, 40, 230), 40);
});

test("the heat-up bar runs from cold to the target", () => {
  assert.equal(progress(40, 190, 40), 0);
  assert.equal(progress(115, 190, 40), 0.5);
  assert.equal(progress(190, 190, 40), 1);
  // Never past the ends, hotter or colder than the range.
  assert.equal(progress(210, 190, 40), 1);
  assert.equal(progress(20, 190, 40), 0);
  // Unknown temperatures leave the bar empty instead of guessing.
  assert.equal(progress(undefined, 190, 40), undefined);
  assert.equal(progress(100, undefined, 40), undefined);
  // A target at the bottom of the range has no span to measure along.
  assert.equal(progress(60, 40, 40), 1);
  assert.equal(progress(30, 40, 40), 0);
});

test("a heating Volcano: numbers, bar, both buttons and presets", () => {
  const hass = volcano({
    states: [
      state("climate.s_b_volcano_h", "heat", {
        friendly_name: "S&B VOLCANO H",
        hvac_action: "heating",
        current_temperature: 115,
        temperature: 185,
        min_temp: 40,
        max_temp: 230,
        fan_modes: ["off", "on"],
        fan_mode: "on",
      }),
    ],
  });
  hass.localize = (key) =>
    ({
      "component.climate.entity_component._.state.heat": "Heat",
      [`component.${OURS}.entity.switch.fan.name`]: "Fan",
    })[key] ?? "";

  const model = cardModel(hass, resolveDevice(hass, "dev1"));

  assert.equal(model.name, "S&B VOLCANO H");
  assert.equal(model.available, true);
  assert.equal(model.unit, "°C");
  assert.equal(model.current, 115);
  assert.equal(model.target, 185);
  assert.equal(model.progress, 75 / 145);
  assert.deepEqual(model.heat, { on: true, label: "Heat" });
  assert.deepEqual(model.air, { on: true, label: "Fan" });
  assert.equal(model.status.kind, "action");
  assert.deepEqual(model.extras, []);
  assert.deepEqual(model.presets, [
    { value: 175, active: false },
    { value: 185, active: true },
    { value: 195, active: false },
    { value: 205, active: false },
  ]);
});

test("a card's own name and presets win", () => {
  const hass = volcano();
  const model = cardModel(hass, resolveDevice(hass, "dev1"), {
    name: "Living room",
    presets: ["180", "200"],
  });
  assert.equal(model.name, "Living room");
  assert.deepEqual(
    model.presets.map((preset) => preset.value),
    [180, 200]
  );
  // Off and idle: neither button is lit.
  assert.equal(model.heat.on, false);
  assert.equal(model.air.on, false);
  // No temperatures reported yet, so no bar.
  assert.equal(model.progress, undefined);
});

test("an unreachable device keeps its controls dark and says why", () => {
  const hass = volcano({
    states: [
      state("climate.s_b_volcano_h", "unavailable"),
      state("binary_sensor.living_room_connection", "off"),
      state("sensor.living_room_link_health", "not_visible"),
    ],
  });
  hass.config = { unit_system: { temperature: "°F" } };
  const model = cardModel(hass, resolveDevice(hass, "dev1"));

  assert.equal(model.available, false);
  assert.equal(model.status.kind, "connection");
  assert.equal(model.heat.on, false);
  assert.equal(model.air, undefined);
  assert.equal(model.name, "Storz & Bickel");
  // The device's limits are unknown, so the unit's own are used.
  assert.deepEqual([model.unit, model.min, model.max], ["°F", 104, 446]);
});

test("a portable has no air button, and shows its battery and find", () => {
  const hass = hassOf(
    [
      entry("climate.crafty", "dev1"),
      entry("sensor.crafty_battery", "dev1", "battery"),
      entry("button.crafty_find", "dev1", "find"),
    ],
    [
      state("climate.crafty", "off", { hvac_action: "off", max_temp: 210, min_temp: 40 }),
      state("sensor.crafty_battery", "82"),
      state("button.crafty_find", "unknown"),
    ]
  );
  const model = cardModel(hass, resolveDevice(hass, "dev1"));

  assert.equal(model.air, undefined);
  assert.deepEqual(
    model.extras.map((item) => item.kind),
    ["battery", "find"]
  );
  assert.equal(model.max, 210);
  assert.equal(model.heat.label, "Heat");
});

test("the status row says what the device is doing", () => {
  const off = volcano();
  assert.deepEqual(statusItems(off, resolveDevice(off, "dev1")), [
    { kind: "action", tone: "neutral", entity: "climate.s_b_volcano_h", label: "<off>" },
  ]);

  const heating = volcano({
    states: [state("climate.s_b_volcano_h", "heat", { hvac_action: "heating" })],
  });
  assert.deepEqual(statusItems(heating, resolveDevice(heating, "dev1")), [
    { kind: "action", tone: "active", entity: "climate.s_b_volcano_h", label: "<heating>" },
  ]);

  const ready = volcano({
    states: [
      state("climate.s_b_volcano_h", "heat", { hvac_action: "idle" }),
      state("binary_sensor.living_room_ready", "on"),
    ],
  });
  assert.deepEqual(statusItems(ready, resolveDevice(ready, "dev1")), [
    { kind: "ready", tone: "success", entity: "binary_sensor.living_room_ready", label: "Ready" },
  ]);
});

test("a climate entity that reports no action falls back to its state", () => {
  const hass = volcano({ states: [state("climate.s_b_volcano_h", "heat")] });
  assert.equal(statusItems(hass, resolveDevice(hass, "dev1"))[0].label, "<heat>");
});

test("a dropped link is explained in the link-health sensor's words", () => {
  const hass = volcano({
    states: [
      state("climate.s_b_volcano_h", "unavailable"),
      state("binary_sensor.living_room_connection", "off"),
      state("sensor.living_room_link_health", "connect_failed"),
    ],
  });
  assert.deepEqual(statusItems(hass, resolveDevice(hass, "dev1")), [
    {
      kind: "connection",
      tone: "warning",
      entity: "sensor.living_room_link_health",
      label: "<connect_failed>",
    },
  ]);
});

test("without a link-health sensor the connection sensor speaks, then nothing", () => {
  const noHealth = hassOf(
    [
      entry("climate.v", "dev1"),
      entry("binary_sensor.v_connection", "dev1", "connection"),
    ],
    [state("climate.v", "unavailable"), state("binary_sensor.v_connection", "off")]
  );
  assert.deepEqual(statusItems(noHealth, resolveDevice(noHealth, "dev1")), [
    { kind: "connection", tone: "warning", entity: "binary_sensor.v_connection", label: "<off>" },
  ]);

  const bare = hassOf([entry("climate.v", "dev1")], []);
  assert.deepEqual(statusItems(bare, resolveDevice(bare, "dev1")), [
    { kind: "connection", tone: "warning", entity: "climate.v", label: "Unavailable" },
  ]);
});

test("unavailable with the link up is not blamed on the link", () => {
  const hass = volcano({ states: [state("climate.s_b_volcano_h", "unavailable")] });
  assert.deepEqual(statusItems(hass, resolveDevice(hass, "dev1")), [
    { kind: "connection", tone: "warning", entity: "climate.s_b_volcano_h", label: "Unavailable" },
  ]);
});

test("a portable adds its battery and, where it can, find", () => {
  const hass = hassOf(
    [
      entry("climate.crafty", "dev1"),
      entry("sensor.crafty_battery", "dev1", "battery"),
      entry("button.crafty_find", "dev1", "find"),
    ],
    [
      state("climate.crafty", "off", { hvac_action: "off" }),
      state("sensor.crafty_battery", "82"),
      state("button.crafty_find", "unknown"),
    ]
  );
  const items = statusItems(hass, resolveDevice(hass, "dev1"));
  assert.deepEqual(
    items.map((item) => [item.kind, item.label]),
    [
      ["action", "<off>"],
      ["battery", "<82>"],
      ["find", "Find device"],
    ]
  );
  assert.equal(items[2].press, true);

  // A Crafty that is not a Crafty+ cannot be told to buzz.
  hass.states["button.crafty_find"].state = "unavailable";
  hass.states["sensor.crafty_battery"].state = "unknown";
  assert.deepEqual(
    statusItems(hass, resolveDevice(hass, "dev1")).map((item) => item.kind),
    ["action"]
  );
});

test("a name that is not translated falls back to English", () => {
  const hass = volcano({ states: [state("binary_sensor.living_room_ready", "on")] });
  hass.localize = undefined;
  assert.equal(statusItems(hass, resolveDevice(hass, "dev1"))[0].label, "Ready");
});

test("a new card starts on the first device, and works with none", () => {
  assert.deepEqual(stubConfig(volcano()), {
    type: `custom:${CARD_TYPE}`,
    device_id: "dev1",
  });
  assert.deepEqual(stubConfig({}), { type: `custom:${CARD_TYPE}`, device_id: "" });
});

test("the card is suggested for our climate entities only", () => {
  const hass = volcano();
  assert.deepEqual(entitySuggestion(hass, "climate.s_b_volcano_h"), {
    config: { type: `custom:${CARD_TYPE}`, device_id: "dev1" },
  });
  assert.equal(entitySuggestion(hass, "climate.hallway"), null);
  assert.equal(entitySuggestion(hass, "switch.s_b_volcano_h_fan"), null);
});

test("the status row is offered on our climate entities only", () => {
  const hass = volcano();
  assert.equal(statusSupported(hass, { entity_id: "climate.s_b_volcano_h" }), true);
  assert.equal(statusSupported(hass, { entity_id: "climate.hallway" }), false);
  assert.equal(statusSupported(hass, { entity_id: "switch.s_b_volcano_h_fan" }), false);
  assert.equal(statusSupported(hass, { area_id: "kitchen" }), false);
  assert.equal(statusSupported(hass, undefined), false);
});
