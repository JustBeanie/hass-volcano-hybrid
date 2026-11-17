/**
 * The Storz & Bickel dashboard card.
 *
 * A card for a vaporizer rather than a thermostat: the temperature it is at,
 * a bar that fills as it heats toward the target, one button each for heat
 * and air, and the temperatures you actually use as one-tap presets.
 *
 * It is drawn with Home Assistant's own building blocks and nothing else:
 * `ha-card` for the surface, theme variables for every color, and the same
 * height, radius and spacing the built-in card features use for controls. So
 * it sits on a dashboard like a native card, in any theme, without looking
 * like the thermostat card.
 *
 * The status row (ready, why it is not connected, battery, find) is also a
 * card feature of its own, for anyone who would rather build on a tile card.
 *
 * What to show is decided in card-logic.js; this file only builds elements.
 */

// The logic is imported with this file's own `?v=<version>`. A plain
// "./card-logic.js" has the same URL in every release, so Home Assistant's
// service worker went on serving the old one beside a new card after an
// update.
const {
  CARD_TYPE,
  DOMAIN,
  STATUS_TYPE,
  cardModel,
  deviceOf,
  entitySuggestion,
  resolveDevice,
  sameSetting,
  statusItems,
  statusSupported,
  stepTarget,
  stubConfig,
} = await import(
  new URL(`./card-logic.js${new URL(import.meta.url).search}`, import.meta.url)
    .href
);

const TONES = {
  success: "var(--success-color)",
  active: "var(--state-climate-heat-color, var(--warning-color))",
  warning: "var(--warning-color)",
  neutral: "var(--state-inactive-color, var(--disabled-color))",
};

// How long after the last press of plus or minus the new target is sent, so
// a run of presses is one command over Bluetooth and not one each.
const TARGET_SEND_DELAY = 600;
// How long a target that was sent is shown before the device has confirmed
// it. After that the card goes back to what the device reports, so a command
// that failed does not leave a target on screen that was never set.
const TARGET_CONFIRM_WINDOW = 8000;

const CONTROL_STYLE = `
  button {
    font: inherit;
    color: inherit;
    margin: 0;
    border: none;
    cursor: pointer;
    -webkit-tap-highlight-color: transparent;
  }
  button:disabled {
    cursor: default;
    opacity: 0.4;
  }
  button:focus-visible {
    outline: 2px solid var(--tone, var(--primary-color));
    outline-offset: 2px;
  }
`;

const CARD_STYLE = `
  :host {
    display: block;
    --heat: var(--state-climate-heat-color, var(--warning-color));
    --air: var(--state-fan-on-color, var(--state-active-color, var(--primary-color)));
    --control-height: var(--feature-height, 42px);
    --control-radius: var(--feature-border-radius, 12px);
    --control-gap: var(--feature-button-spacing, 12px);
    --idle-fill: color-mix(in srgb, var(--disabled-color) 20%, transparent);
  }
  ha-card {
    height: 100%;
    box-sizing: border-box;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  ${CONTROL_STYLE}

  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0;
    background: none;
    text-align: start;
    min-width: 0;
  }
  .header ha-state-icon {
    flex: none;
    --mdc-icon-size: 24px;
    color: var(--tone);
  }
  .name {
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--ha-font-size-l, 16px);
    font-weight: var(--ha-font-weight-medium, 500);
    color: var(--primary-text-color);
  }
  .status {
    flex: none;
    max-width: 55%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: var(--ha-font-size-s, 12px);
    font-weight: var(--ha-font-weight-medium, 500);
    color: var(--primary-text-color);
    background: color-mix(in srgb, var(--tone) 20%, transparent);
  }

  .readout {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
  }
  .current {
    font-size: 48px;
    line-height: 1;
    font-weight: var(--ha-font-weight-normal, 400);
    color: var(--primary-text-color);
    white-space: nowrap;
  }
  .current .unit, .target .unit {
    font-size: 0.45em;
    vertical-align: top;
    color: var(--secondary-text-color);
    margin-inline-start: 2px;
  }
  .target {
    display: flex;
    align-items: center;
    gap: 4px;
  }
  .target .value {
    min-width: 3.2em;
    text-align: center;
    font-size: var(--ha-font-size-xl, 20px);
    font-weight: var(--ha-font-weight-medium, 500);
    color: var(--primary-text-color);
    white-space: nowrap;
  }
  .step {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--idle-fill);
    font-size: 20px;
    line-height: 1;
  }

  .bar {
    height: 8px;
    border-radius: 4px;
    background: var(--idle-fill);
    overflow: hidden;
  }
  .bar > div {
    height: 100%;
    border-radius: 4px;
    background: var(--fill);
    transition: width 0.6s ease;
  }
  @media (prefers-reduced-motion: reduce) {
    .bar > div { transition: none; }
  }

  .buttons, .presets, .extras {
    display: flex;
    gap: var(--control-gap);
  }
  .toggle {
    flex: 1 1 0;
    min-width: 0;
    height: var(--control-height);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    border-radius: var(--control-radius);
    background: var(--idle-fill);
    font-weight: var(--ha-font-weight-medium, 500);
  }
  .toggle[aria-pressed="true"] {
    background: color-mix(in srgb, var(--tone) 30%, transparent);
  }
  .toggle ha-state-icon, .toggle ha-icon {
    --mdc-icon-size: 20px;
    color: var(--secondary-text-color);
  }
  .toggle[aria-pressed="true"] ha-state-icon,
  .toggle[aria-pressed="true"] ha-icon {
    color: var(--tone);
  }

  .lists {
    display: flex;
    gap: 4px;
    overflow-x: auto;
    scrollbar-width: none;
    margin-bottom: -8px;
  }
  .list {
    flex: none;
    height: 28px;
    padding: 0 12px;
    border-radius: 999px;
    background: none;
    color: var(--secondary-text-color);
    font-size: var(--ha-font-size-s, 12px);
    white-space: nowrap;
  }
  .list[aria-selected="true"] {
    background: var(--idle-fill);
    color: var(--primary-text-color);
    font-weight: var(--ha-font-weight-medium, 500);
  }

  .presets {
    flex-wrap: wrap;
    gap: 8px;
  }
  .preset {
    flex: 1 1 0;
    min-width: 56px;
    height: 32px;
    border-radius: 999px;
    background: var(--idle-fill);
    font-size: var(--ha-font-size-m, 14px);
  }
  .preset[aria-pressed="true"] {
    background: color-mix(in srgb, var(--heat) 30%, transparent);
    font-weight: var(--ha-font-weight-medium, 500);
  }

  .extra {
    flex: 1 1 0;
    min-width: 0;
    height: 32px;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    border-radius: 999px;
    background: var(--idle-fill);
    font-size: var(--ha-font-size-s, 12px);
  }
  .extra ha-state-icon {
    --mdc-icon-size: 16px;
    color: var(--secondary-text-color);
  }

  .unavailable .current, .unavailable .target .value {
    color: var(--disabled-text-color, var(--secondary-text-color));
  }
  .message {
    color: var(--secondary-text-color);
  }
`;

/** Build an element with properties, attributes and children in one go. */
function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined) continue;
    if (key === "class") node.className = value;
    else if (/^(aria-|data-)|^(role|type)$/.test(key)) {
      node.setAttribute(key, value);
    } else node[key] = value;
  }
  node.append(...children.filter((child) => child !== undefined));
  return node;
}

// How often an element showing the auto-off countdown redraws by itself. The
// label is in whole minutes; nothing else would move it between state changes.
const TICK_INTERVAL = 15000;

/** Keep `element._update` running on a timer while `ticking`, and only then. */
function keepTicking(element, ticking) {
  if (ticking && element.isConnected && !element._tick) {
    element._tick = setInterval(() => element._update(), TICK_INTERVAL);
  } else if ((!ticking || !element.isConnected) && element._tick) {
    clearInterval(element._tick);
    element._tick = undefined;
  }
}

const hasTimer = (items) => items.some((item) => item.kind === "auto_off");

/** Press a status item's button entity, or open its entity's dialog. */
function activate(source, hass, item) {
  if (item.press) {
    hass.callService("button", "press", { entity_id: item.entity });
  } else moreInfo(source, item.entity);
}

function moreInfo(source, entityId) {
  source.dispatchEvent(
    new CustomEvent("hass-more-info", {
      detail: { entityId },
      bubbles: true,
      composed: true,
    })
  );
}

function stateIcon(hass, entityId) {
  const icon = document.createElement("ha-state-icon");
  icon.hass = hass;
  icon.stateObj = hass.states[entityId];
  return icon;
}

class StorzBickelCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = CARD_STYLE;
    this._card = document.createElement("ha-card");
    this.shadowRoot.append(style, this._card);
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid configuration");
    }
    this._config = config;
    this._key = undefined;
    this._list = undefined;
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  connectedCallback() {
    this._update();
  }

  disconnectedCallback() {
    clearTimeout(this._sendTimer);
    keepTicking(this, false);
  }

  // Redraws only when what is shown has changed: `hass` is set on every
  // state change anywhere in the house.
  _update() {
    if (!this._hass || !this._config) return;
    const resolved = resolveDevice(this._hass, this._config.device_id);
    const model = resolved
      ? cardModel(this._hass, resolved, this._config, {
          list: this._chosenList(),
        })
      : null;
    keepTicking(this, Boolean(model && hasTimer(model.extras)));
    // A target being stepped is shown as stepped until the device catches up.
    if (model && this._pendingTarget !== undefined) {
      const sent = !this._sendTimer;
      if (
        sent &&
        (sameSetting(model.target, this._pendingTarget, model.unit) ||
          Date.now() > this._pendingUntil)
      ) {
        this._pendingTarget = undefined;
      } else {
        model.target = this._pendingTarget;
        model.presets = model.presets.map((preset) => ({
          ...preset,
          active: sameSetting(preset.value, this._pendingTarget, model.unit),
        }));
      }
    }
    const key = JSON.stringify(model);
    if (key === this._key) return;
    this._key = key;
    this._model = model;
    this._render(model);
  }

  _render(model) {
    if (!model) {
      this._card.className = "";
      this._card.replaceChildren(
        el("div", {
          class: "message",
          textContent:
            this._hass.localize?.(
              "ui.panel.lovelace.warning.entity_not_found",
              { entity: this._config.device_id || "Storz & Bickel" }
            ) || "Choose a Storz & Bickel device",
        })
      );
      return;
    }

    const hass = this._hass;
    const off = !model.available;
    const tone = TONES[model.status.tone];
    this._card.className = off ? "unavailable" : "";

    const header = el(
      "button",
      {
        class: "header",
        type: "button",
        onclick: () => moreInfo(this, model.entity),
      },
      stateIcon(hass, model.entity),
      el("span", { class: "name", textContent: model.name }),
      el("span", { class: "status", textContent: model.status.label })
    );
    header.style.setProperty("--tone", tone);

    const temperature = (value) => [
      value === undefined ? "–" : String(Math.round(value)),
      el("span", { class: "unit", textContent: model.unit }),
    ];
    const step = (delta, label, text) =>
      el("button", {
        class: "step",
        "data-key": `step${delta}`,
        type: "button",
        textContent: text,
        disabled: off || model.target === undefined,
        "aria-label": label,
        onclick: () => this._step(delta),
      });
    const readout = el(
      "div",
      { class: "readout" },
      el("div", { class: "current" }, ...temperature(model.current)),
      el(
        "div",
        { class: "target" },
        step(-1, "Lower target", "−"),
        el("span", { class: "value" }, ...temperature(model.target)),
        step(1, "Raise target", "+")
      )
    );

    const fraction = model.progress ?? 0;
    const fill = el("div");
    fill.style.width = `${Math.round(fraction * 100)}%`;
    // Heat color while it is heating, the "reached" color once it is
    // there, and muted when the heater is off and it is only cooling.
    fill.style.setProperty(
      "--fill",
      !model.heat.on
        ? "var(--state-inactive-color, var(--disabled-color))"
        : model.status.kind === "ready"
          ? "var(--success-color)"
          : "var(--heat)"
    );
    const bar = el(
      "div",
      {
        class: "bar",
        role: "progressbar",
        "aria-valuemin": "0",
        "aria-valuemax": "100",
        "aria-valuenow": String(Math.round(fraction * 100)),
        "aria-label": model.status.label,
      },
      fill
    );

    const toggle = (control, toneColor, icon, onclick) => {
      const button = el(
        "button",
        {
          class: "toggle",
          "data-key": icon,
          type: "button",
          disabled: off,
          "aria-pressed": String(control.on),
          onclick,
        },
        el("ha-icon", { icon }),
        el("span", { textContent: control.label })
      );
      button.style.setProperty("--tone", toneColor);
      return button;
    };
    const buttons = el(
      "div",
      { class: "buttons" },
      toggle(model.heat, "var(--heat)", "mdi:fire", () =>
        this._call("set_hvac_mode", {
          hvac_mode: model.heat.on ? "off" : "heat",
        })
      ),
      model.air &&
        toggle(model.air, "var(--air)", "mdi:fan", () =>
          this._call("set_fan_mode", { fan_mode: model.air.on ? "off" : "on" })
        )
    );

    const rows = [header, readout, bar, buttons];

    if (model.lists.length > 0) {
      rows.push(
        el(
          "div",
          { class: "lists", role: "tablist" },
          ...model.lists.map((list, index) =>
            el("button", {
              class: "list",
              "data-key": `list${index}`,
              type: "button",
              role: "tab",
              "aria-selected": String(list.active),
              textContent: list.name,
              onclick: () => this._chooseList(index),
            })
          )
        )
      );
    }

    if (model.presets.length > 0) {
      rows.push(
        el(
          "div",
          { class: "presets" },
          ...model.presets.map((preset) =>
            el("button", {
              class: "preset",
              "data-key": `preset${preset.value}`,
              type: "button",
              disabled: off,
              "aria-pressed": String(preset.active),
              textContent: `${preset.value}°`,
              onclick: () => this._setTarget(preset.value, 0),
            })
          )
        )
      );
    }

    if (model.extras.length > 0) {
      rows.push(
        el(
          "div",
          { class: "extras" },
          ...model.extras.map((item) =>
            el(
              "button",
              {
                class: "extra",
                type: "button",
                onclick: () => activate(this, hass, item),
              },
              stateIcon(hass, item.entity),
              el("span", { textContent: item.label })
            )
          )
        )
      );
    }

    // Redrawing replaces the buttons, so put the keyboard focus back on the
    // one that had it: stepping the target with the keyboard redraws each time.
    const focused = this.shadowRoot.activeElement?.dataset?.key;
    this._card.replaceChildren(...rows);
    if (focused) {
      this._card.querySelector(`[data-key="${focused}"]`)?.focus();
    }
  }

  // The preset list picked on this card is remembered per browser and
  // device, so it survives a reload. Storage can be missing or refuse, and
  // then the choice lasts as long as the page.
  _storageKey() {
    return `${CARD_TYPE}:list:${this._config.device_id ?? ""}`;
  }

  _chosenList() {
    if (this._list === undefined) {
      try {
        const saved = window.localStorage.getItem(this._storageKey());
        this._list = saved === null ? null : Number.parseInt(saved, 10);
      } catch {
        this._list = null;
      }
    }
    return this._list ?? undefined;
  }

  _chooseList(index) {
    this._list = index;
    try {
      window.localStorage.setItem(this._storageKey(), String(index));
    } catch {
      // Remembered for this page only.
    }
    this._update();
  }

  _call(service, data) {
    this._hass.callService("climate", service, {
      entity_id: this._model.entity,
      ...data,
    });
  }

  _step(delta) {
    const model = this._model;
    this._setTarget(
      stepTarget(model.target, delta, model.min, model.max, model.unit),
      TARGET_SEND_DELAY
    );
  }

  _setTarget(value, delay) {
    this._pendingTarget = value;
    clearTimeout(this._sendTimer);
    this._sendTimer = setTimeout(() => {
      this._sendTimer = undefined;
      this._pendingUntil = Date.now() + TARGET_CONFIRM_WINDOW;
      this._call("set_temperature", { temperature: value });
    }, delay);
    this._update();
  }

  getCardSize() {
    return 5;
  }

  getGridOptions() {
    return { columns: 12, rows: "auto", min_columns: 6 };
  }

  static getStubConfig(hass) {
    return stubConfig(hass);
  }

  // Home Assistant builds the editor from this: a device picker limited to
  // this integration's devices, an optional name and the presets.
  static getConfigForm() {
    return {
      schema: [
        {
          name: "device_id",
          required: true,
          selector: { device: { integration: DOMAIN } },
        },
        { name: "name", selector: { text: {} } },
        // Off unless set, so a card without it shows presets as before.
        { name: "hide_presets", selector: { boolean: {} } },
        { name: "presets", selector: { text: { multiple: true } } },
        {
          name: "preset_lists",
          selector: {
            object: {
              multiple: true,
              label_field: "name",
              fields: {
                name: { required: true, selector: { text: {} } },
                presets: { selector: { text: { multiple: true } } },
              },
            },
          },
        },
      ],
      computeLabel: (schema, localize) =>
        ({
          device_id:
            localize("ui.panel.lovelace.editor.card.generic.device") ||
            localize("ui.common.device") ||
            "Device",
          name: localize("ui.panel.lovelace.editor.card.generic.name") || "Name",
          hide_presets: "Hide presets",
          presets: "Temperature presets",
          preset_lists: "Preset lists",
        })[schema.name],
      computeHelper: (schema) =>
        ({
          hide_presets:
            "Show no preset buttons, whatever the lists below hold.",
          presets:
            "One-tap targets, in the temperature unit Home Assistant uses. Leave empty for the defaults.",
          preset_lists:
            "Optional named sets of presets. With two or more, the card shows a tab for each; they replace the presets above.",
        })[schema.name],
    };
  }
}

class StorzBickelStatus extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    const style = document.createElement("style");
    style.textContent = `
      :host {
        display: block;
      }
      ${CONTROL_STYLE}
      .row {
        display: flex;
        gap: var(--feature-button-spacing, 12px);
        height: var(--feature-height, 42px);
      }
      .item {
        flex: 1 1 0;
        min-width: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 8px;
        box-sizing: border-box;
        height: 100%;
        padding: 0 12px;
        border-radius: var(--feature-border-radius, 12px);
        background: color-mix(in srgb, var(--tone) 20%, transparent);
        color: var(--primary-text-color);
        font-size: var(--ha-font-size-m, 14px);
      }
      .item ha-state-icon {
        flex: none;
        --mdc-icon-size: 20px;
        color: var(--tone);
      }
      .label {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }
    `;
    this._row = document.createElement("div");
    this._row.className = "row";
    this.shadowRoot.append(style, this._row);
  }

  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._key = undefined;
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  set context(context) {
    this._context = context;
    this._update();
  }

  connectedCallback() {
    this._update();
  }

  disconnectedCallback() {
    keepTicking(this, false);
  }

  _update() {
    if (!this._hass || !this._context?.entity_id) return;
    const deviceId = deviceOf(this._hass, this._context.entity_id);
    const resolved = resolveDevice(this._hass, deviceId);
    const items = resolved ? statusItems(this._hass, resolved) : [];
    keepTicking(this, hasTimer(items));
    const key = JSON.stringify(items);
    if (key === this._key) return;
    this._key = key;
    this._row.replaceChildren(...items.map((item) => this._item(item)));
  }

  _item(item) {
    const button = el(
      "button",
      {
        class: "item",
        type: "button",
        title: item.label,
        onclick: (event) => {
          // Otherwise the card underneath opens its own dialog as well.
          event.stopPropagation();
          activate(this, this._hass, item);
        },
      },
      stateIcon(this._hass, item.entity),
      el("span", { class: "label", textContent: item.label })
    );
    button.style.setProperty("--tone", TONES[item.tone]);
    return button;
  }

  static getStubConfig() {
    return { type: `custom:${STATUS_TYPE}` };
  }
}

// Guarded: a dashboard can load this file more than once.
if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, StorzBickelCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    name: "Storz & Bickel",
    description:
      "Temperature, a heat-up bar, heat, air and presets for a Volcano Hybrid, Crafty, Venty or Veazy.",
    preview: true,
    documentationURL:
      "https://github.com/JustBeanie/hass-volcano-hybrid#dashboard-card",
    getEntitySuggestion: entitySuggestion,
  });
}

if (!customElements.get(STATUS_TYPE)) {
  customElements.define(STATUS_TYPE, StorzBickelStatus);
  window.customCardFeatures = window.customCardFeatures || [];
  window.customCardFeatures.push({
    type: STATUS_TYPE,
    name: "Storz & Bickel status",
    isSupported: statusSupported,
  });
}
