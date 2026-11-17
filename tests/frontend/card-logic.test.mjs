// What the dashboard card shows, tested without a browser: `node --test`.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CARD_TYPE,
  STATUS_TYPE,
  deviceOf,
  devices,
  entitySuggestion,
  resolveDevice,
  statusItems,
  statusSupported,
  stubConfig,
  thermostatConfig,
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

test("a Volcano gets the built-in dial, heat switch and air switch", () => {
  const hass = volcano();
  assert.deepEqual(thermostatConfig(hass, resolveDevice(hass, "dev1")), {
    type: "thermostat",
    entity: "climate.s_b_volcano_h",
    features: [
      { type: "climate-hvac-modes", style: "icons" },
      { type: "climate-fan-modes", style: "icons" },
      { type: `custom:${STATUS_TYPE}` },
    ],
  });
});

test("a portable has no air pump, so no air switch", () => {
  const hass = hassOf(
    [entry("climate.crafty", "dev1")],
    [state("climate.crafty", "off", { hvac_action: "off" })]
  );
  const config = thermostatConfig(hass, resolveDevice(hass, "dev1"), {
    name: "Bedside",
  });
  assert.deepEqual(
    config.features.map((feature) => feature.type),
    ["climate-hvac-modes", `custom:${STATUS_TYPE}`]
  );
  assert.equal(config.name, "Bedside");
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
