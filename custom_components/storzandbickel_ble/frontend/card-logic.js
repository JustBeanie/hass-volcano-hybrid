/**
 * What the Storz & Bickel card shows, with no DOM in it.
 *
 * Everything here takes the `hass` object the dashboard hands a card and
 * returns plain data, so it runs under `node --test` as it stands. The
 * elements in storzandbickel-card.js only turn the results into markup.
 */

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

/**
 * The configuration of the built-in thermostat card this card renders.
 *
 * The dial, the heat switch and the air switch are all Home Assistant's own;
 * only the status row is ours. A portable has no air pump, so its climate
 * entity offers no fan modes and gets no air switch.
 */
export function thermostatConfig(hass, resolved, config = {}) {
  const features = [{ type: "climate-hvac-modes", style: "icons" }];
  const fanModes = hass.states?.[resolved.climate]?.attributes?.fan_modes;
  if (Array.isArray(fanModes) && fanModes.length > 0) {
    features.push({ type: "climate-fan-modes", style: "icons" });
  }
  features.push({ type: `custom:${STATUS_TYPE}` });

  const card = { type: "thermostat", entity: resolved.climate, features };
  if (config.name) card.name = config.name;
  return card;
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
    const health = stateOf(hass, resolved.linkHealth);
    items.push({
      kind: "connection",
      tone: "warning",
      entity: (known(health) ? resolved.linkHealth : resolved.connection) ??
        resolved.climate,
      label: known(health)
        ? hass.formatEntityState(health)
        : connection
          ? hass.formatEntityState(connection)
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
