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
  autoOffTime: ["number", "auto_off_time"],
  autoOffCountdown: ["sensor", "auto_off_countdown"],
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
 * How long until the device switches its heater off by itself, while it heats.
 *
 * A portable reports the time left, and that is used as it stands. The
 * Volcano Hybrid reports only the delay it is set to, so the time left is
 * worked out from when the heater was turned on and marked `estimated`. The
 * device's own timer may restart when it is used, so the real switch-off is
 * at or after the estimate, not before -- unless Home Assistant restarted
 * while it was heating, which moves "turned on" to the restart.
 *
 * Undefined while the heater is off or nothing says how long is left.
 */
export function autoOff(hass, resolved, now = Date.now()) {
  const climate = stateOf(hass, resolved.climate);
  if (!known(climate) || climate.state !== "heat") return undefined;

  const countdown = stateOf(hass, resolved.autoOffCountdown);
  const left = known(countdown) ? Number.parseFloat(countdown.state) : NaN;
  if (Number.isFinite(left)) {
    return {
      entity: resolved.autoOffCountdown,
      seconds: Math.max(0, left),
      estimated: false,
    };
  }

  const delay = stateOf(hass, resolved.autoOffTime);
  const minutes = known(delay) ? Number.parseFloat(delay.state) : NaN;
  const since = Date.parse(climate.last_changed);
  if (!Number.isFinite(minutes) || !Number.isFinite(since)) return undefined;
  const elapsed = Math.max(0, (now - since) / 1000);
  return {
    entity: resolved.autoOffTime,
    seconds: Math.max(0, minutes * 60 - elapsed),
    estimated: true,
  };
}

/** "Auto-off in 42 min", "Auto-off in ~1 h 5 min", in whole minutes, rounded up. */
export function autoOffLabel(timer) {
  const total = Math.ceil(timer.seconds / 60);
  if (total <= 0) return "Auto-off due";
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  const text =
    hours === 0
      ? `${minutes} min`
      : minutes === 0
        ? `${hours} h`
        : `${hours} h ${minutes} min`;
  return `Auto-off in ${timer.estimated ? "~" : ""}${text}`;
}

/**
 * What the status row shows, left to right.
 *
 * Each item names the entity it speaks for, so the row can open that
 * entity's own dialog and show its own icon. `tone` is one of `success`,
 * `active`, `warning` or `neutral`; the element maps it to a theme color.
 */
export function statusItems(hass, resolved, now = Date.now()) {
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

  const timer = autoOff(hass, resolved, now);
  if (timer) {
    items.push({
      kind: "auto_off",
      tone: "neutral",
      entity: timer.entity,
      label: autoOffLabel(timer),
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
export function presets(config, unit, min, max, fallback = true) {
  const raw = config?.presets;
  // A list from the editor, or "175, 185" written by hand in YAML.
  const given = Array.isArray(raw)
    ? raw
    : typeof raw === "string"
      ? raw.split(/[\s,;]+/)
      : [];
  const parsed = given
    .map((value) => Number.parseFloat(value))
    .filter((value) => Number.isFinite(value));
  const values =
    parsed.length > 0 ? parsed : fallback ? DEFAULT_PRESETS[unit] : [];
  return [...new Set(values)]
    .filter((value) => value >= min && value <= max)
    .sort((left, right) => left - right);
}

/**
 * The preset lists to offer, each `{ name, values }`.
 *
 * A card can carry several named lists under `preset_lists` and switch
 * between them; a list with nothing usable in it is left out. Without any,
 * there is one unnamed list: the card's `presets`, or the defaults.
 * `presets: false` turns presets off altogether, named lists included.
 */
export function presetLists(config, unit, min, max) {
  if (config?.presets === false) return [];
  const given = Array.isArray(config?.preset_lists) ? config.preset_lists : [];
  const named = given
    .map((list, index) => ({
      name: String(list?.name ?? "").trim() || `Presets ${index + 1}`,
      values: presets(list, unit, min, max, false),
    }))
    .filter((list) => list.values.length > 0);
  return named.length > 0
    ? named
    : [{ name: undefined, values: presets(config, unit, min, max) }];
}

/**
 * Which list to show: the one asked for if it exists, else the first that
 * holds the current target, else the first.
 */
export function chosenList(lists, requested, target) {
  if (Number.isInteger(requested) && requested >= 0 && requested < lists.length) {
    return requested;
  }
  const holding = lists.findIndex((list) => list.values.includes(target));
  return holding === -1 ? 0 : holding;
}

/**
 * The whole degree Celsius the device stores for a target in ``unit``.
 *
 * Every Storz & Bickel device sets its target in whole °C, so in °F only
 * every 1.8° is a setting of its own: 379 °F and 380 °F are both 193 °C.
 */
export function deviceDegree(value, unit) {
  return Math.round(unit === "°F" ? ((value - 32) * 5) / 9 : value);
}

/** Whether two targets are the same setting on the device. */
export function sameSetting(left, right, unit) {
  return (
    left !== undefined &&
    right !== undefined &&
    deviceDegree(left, unit) === deviceDegree(right, unit)
  );
}

/**
 * A target moved by ``delta`` device degrees, kept inside what the device
 * accepts. In °F one step is one degree Celsius, about 2 °F: a step of 1 °F
 * often lands on the setting it started from, and the press does nothing.
 */
export function stepTarget(target, delta, min, max, unit = "°C") {
  const next =
    unit === "°F"
      ? Math.round(((deviceDegree(target, unit) + delta) * 9) / 5 + 32)
      : Math.round(target + delta);
  return Math.min(max, Math.max(min, next));
}

/**
 * How far the block is toward the target, from 0 to 1.
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
 * shows the last known numbers grayed out and disables its controls, apart
 * from the ones the integration keeps usable to bring the link back.
 */
export function cardModel(
  hass,
  resolved,
  config = {},
  { now = Date.now(), list } = {}
) {
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
  const [status, ...extras] = statusItems(hass, resolved, now);
  const lists = presetLists(config, unit, min, max);
  const chosen = chosenList(lists, list, target);

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
    // Tabs to switch lists, only when there is more than one to switch to.
    lists:
      lists.length > 1
        ? lists.map((entry, index) => ({
            name: entry.name,
            active: index === chosen,
          }))
        : [],
    presets: (lists[chosen]?.values ?? []).map((value) => ({
      value,
      active: sameSetting(value, target, unit),
    })),
  };
}
