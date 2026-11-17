/**
 * What the Storz & Bickel card shows, with no DOM in it.
 *
 * Everything here takes the `hass` object the dashboard hands a card and
 * returns plain data, so it runs under `node --test` as it stands. The
 * elements in storzandbickel-card.js only turn the results into markup.
 */

// Targets offered as one-tap presets when the card is not given its own,
// in each unit Home Assistant can be set to. Round numbers in both, not one
// list converted into the other.
const DEFAULT_PRESETS = {
  "°C": [175, 185, 195, 205],
  "°F": [350, 365, 380, 400],
};

export const DOMAIN = "storzandbickel_ble";
export const CARD_TYPE = "storzandbickel-card";
export const STATUS_TYPE = "storzandbickel-status";

// The entities the card uses besides the climate entity, by the domain and
// translation key the integration gives them. Looked up this way, never by
// entity ID: a user is free to rename every one of them.
const ROLES = {
  ready: ["binary_sensor", "ready"],
  connection: ["binary_sensor", "connection"],
  linkHealth: ["sensor", "link_health"],
  battery: ["sensor", "battery"],
  find: ["button", "find"],
};

const domainOf = (entityId) => entityId.split(".", 1)[0];

const ours = (hass) =>
  Object.values(hass?.entities ?? {}).filter(
    (entry) => entry.platform === DOMAIN && entry.device_id
  );

/** The device an entity of this integration belongs to, if it is one. */
export function deviceOf(hass, entityId) {
  const entry = hass?.entities?.[entityId];
  return entry?.platform === DOMAIN ? (entry.device_id ?? undefined) : undefined;
}

/** Every device of this integration that has a climate entity, in a stable order. */
export function devices(hass) {
  const found = new Set();
  for (const entry of ours(hass)) {
    if (domainOf(entry.entity_id) === "climate") found.add(entry.device_id);
  }
  return [...found].sort();
}

/**
 * The entity IDs the card needs for one device.
 *
 * Returns undefined when the device is not one of ours or has no climate
 * entity, which is the one entity the card cannot do without.
 */
export function resolveDevice(hass, deviceId) {
  if (!deviceId) return undefined;
  const resolved = {};
  for (const entry of ours(hass)) {
    if (entry.device_id !== deviceId) continue;
    const domain = domainOf(entry.entity_id);
    if (domain === "climate") {
      resolved.climate = entry.entity_id;
      continue;
    }
    for (const [role, [roleDomain, key]] of Object.entries(ROLES)) {
      if (domain === roleDomain && entry.translation_key === key) {
        resolved[role] = entry.entity_id;
      }
    }
  }
  return resolved.climate ? resolved : undefined;
}

const stateOf = (hass, entityId) =>
  entityId ? hass.states?.[entityId] : undefined;

const known = (stateObj) =>
  stateObj && stateObj.state !== "unavailable" && stateObj.state !== "unknown";

const entityName = (hass, domain, key, fallback) =>
  hass.localize?.(`component.${DOMAIN}.entity.${domain}.${key}.name`) ||
  fallback;

/**
 * What the status row shows, left to right.
 *
 * Each item names the entity it speaks for, so the row can open that
 * entity's own dialog and show its own icon. `tone` is one of `success`,
 * `active`, `warning` or `neutral`; the element maps it to a theme colour.
 */
export function statusItems(hass, resolved) {
  const items = [];
  const climate = stateOf(hass, resolved.climate);
  const connection = stateOf(hass, resolved.connection);
  const ready = stateOf(hass, resolved.ready);

  if (connection?.state === "off" || !known(climate)) {
    // Not connected: say why, in the link-health sensor's own words when it
    // is there, since "refused" and "out of range" call for different fixes.
    // Only while the link really is down, though: if it is up and the device
    // is unavailable for some other reason, "Connected" would be the wrong
    // thing to show as the problem.
    const health = stateOf(hass, resolved.linkHealth);
    const down = connection?.state === "off";
    const speaker = known(health) ? resolved.linkHealth : resolved.connection;
    items.push({
      kind: "connection",
      tone: "warning",
      entity: down ? speaker : resolved.climate,
      label: down
        ? hass.formatEntityState(stateOf(hass, speaker))
        : hass.localize?.("state.default.unavailable") || "Unavailable",
    });
  } else if (ready?.state === "on") {
    items.push({
      kind: "ready",
      tone: "success",
      entity: resolved.ready,
      label: entityName(hass, "binary_sensor", "ready", "Ready"),
    });
  } else {
    const action = climate.attributes?.hvac_action;
    items.push({
      kind: "action",
      tone: action === "heating" ? "active" : "neutral",
      entity: resolved.climate,
      label: action
        ? hass.formatEntityAttributeValue(climate, "hvac_action")
        : hass.formatEntityState(climate),
    });
  }

  const battery = stateOf(hass, resolved.battery);
  if (known(battery)) {
    items.push({
      kind: "battery",
      tone: "neutral",
      entity: resolved.battery,
      label: hass.formatEntityState(battery),
    });
  }

  // The integration marks "find" unavailable on a device that cannot do it.
  const find = stateOf(hass, resolved.find);
  if (find && find.state !== "unavailable") {
    items.push({
      kind: "find",
      tone: "neutral",
      entity: resolved.find,
      label: entityName(hass, "button", "find", "Find device"),
      press: true,
    });
  }
  return items;
}

/** The configuration a newly added card starts with: the first device found. */
export function stubConfig(hass) {
  return { type: `custom:${CARD_TYPE}`, device_id: devices(hass)[0] ?? "" };
}

/** Offer this card when someone picks one of our climate entities to add. */
export function entitySuggestion(hass, entityId) {
  if (domainOf(entityId) !== "climate") return null;
  const deviceId = deviceOf(hass, entityId);
  if (!deviceId) return null;
  return { config: { type: `custom:${CARD_TYPE}`, device_id: deviceId } };
}

/** Whether the status row belongs on the card it is being offered for. */
export function statusSupported(hass, context) {
  const entityId = context?.entity_id;
  return Boolean(
    entityId && domainOf(entityId) === "climate" && deviceOf(hass, entityId)
  );
}

const number = (value) =>
  typeof value === "number" && Number.isFinite(value) ? value : undefined;

/** The unit temperatures are shown and set in: Home Assistant's own. */
export function temperatureUnit(hass) {
  return hass.config?.unit_system?.temperature === "°F" ? "°F" : "°C";
}

/**
 * The presets to offer: the card's own if it lists any, else the defaults.
 *
 * The editor hands them over as text, so anything that is not a number is
 * dropped rather than shown as a button that cannot work. Sorted, without
 * repeats, and limited to what the device accepts.
 */
export function presets(config, unit, min, max) {
  const given = Array.isArray(config?.presets) ? config.presets : [];
  const parsed = given
    .map((value) => Number.parseFloat(value))
    .filter((value) => Number.isFinite(value));
  const values = parsed.length > 0 ? parsed : DEFAULT_PRESETS[unit];
  return [...new Set(values)]
    .filter((value) => value >= min && value <= max)
    .sort((left, right) => left - right);
}

/** A target moved by ``delta``, kept inside what the device accepts. */
export function stepTarget(target, delta, min, max) {
  return Math.min(max, Math.max(min, Math.round(target + delta)));
}

/**
 * How far the block is towards the target, from 0 to 1.
 *
 * Measured from the lowest temperature the device can be set to, which is
 * the nearest thing to "cold" it reports. Undefined when either temperature
 * is unknown, so the bar can be left empty rather than drawn wrong.
 */
export function progress(current, target, min) {
  if (current === undefined || target === undefined) return undefined;
  if (target <= min) return current >= target ? 1 : 0;
  return Math.min(1, Math.max(0, (current - min) / (target - min)));
}

/**
 * Everything the card draws for one device, as plain data.
 *
 * `available` is false while the device cannot be reached; the card then
 * shows the last known numbers greyed out and disables its controls, apart
 * from the ones the integration keeps usable to bring the link back.
 */
export function cardModel(hass, resolved, config = {}) {
  const climate = stateOf(hass, resolved.climate);
  const attributes = climate?.attributes ?? {};
  const available = Boolean(known(climate));
  const unit = temperatureUnit(hass);
  const min = number(attributes.min_temp) ?? (unit === "°F" ? 104 : 40);
  const max = number(attributes.max_temp) ?? (unit === "°F" ? 446 : 230);
  const current = number(attributes.current_temperature);
  const target = number(attributes.temperature);
  const heating = available && climate.state === "heat";
  const fanModes = attributes.fan_modes;
  const hasAir = Array.isArray(fanModes) && fanModes.includes("on");
  const [status, ...extras] = statusItems(hass, resolved);

  return {
    entity: resolved.climate,
    name: config.name || attributes.friendly_name || "Storz & Bickel",
    available,
    unit,
    min,
    max,
    current,
    target,
    status,
    extras,
    progress: progress(current, target, min),
    heat: {
      on: heating,
      label:
        hass.localize?.("component.climate.entity_component._.state.heat") ||
        "Heat",
    },
    // A portable has no air pump, so its climate entity has no fan modes.
    air: hasAir
      ? {
          on: available && attributes.fan_mode === "on",
          label: entityName(hass, "switch", "fan", "Air"),
        }
      : undefined,
    presets: presets(config, unit, min, max).map((value) => ({
      value,
      active: value === target,
    })),
  };
}
