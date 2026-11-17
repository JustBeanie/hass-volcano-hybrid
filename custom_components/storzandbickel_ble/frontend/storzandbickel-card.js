/**
 * The Storz & Bickel dashboard card.
 *
 * It draws almost nothing itself. The card is Home Assistant's own thermostat
 * card, set up for one device: the built-in dial, the built-in heat switch
 * and, on a Volcano, the built-in fan-mode switch for the air pump. So it
 * looks and behaves like every other card on the dashboard, in any theme.
 *
 * The one piece Home Assistant has no equivalent for is the status row
 * (ready, why it is not connected, battery, find), which is a card feature
 * and can be added to any tile or thermostat card on its own.
 *
 * What to show is decided in card-logic.js; this file only builds elements.
 */

import {
  CARD_TYPE,
  DOMAIN,
  STATUS_TYPE,
  deviceOf,
  entitySuggestion,
  resolveDevice,
  statusItems,
  statusSupported,
  stubConfig,
  thermostatConfig,
} from "./card-logic.js";

const TONES = {
  success: "var(--success-color)",
  active: "var(--state-climate-heat-color, var(--warning-color))",
  warning: "var(--warning-color)",
  neutral: "var(--state-inactive-color, var(--disabled-color))",
};

class StorzBickelCard extends HTMLElement {
  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid configuration");
    }
    this._config = config;
    this._update();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    this._update();
  }

  // Rebuilds the inner card only when what it should be has changed: `hass`
  // is set on every state change anywhere in the house.
  _update() {
    if (!this._hass || !this._config) return;
    const resolved = resolveDevice(this._hass, this._config.device_id);
    const inner = resolved
      ? thermostatConfig(this._hass, resolved, this._config)
      : null;
    const key = JSON.stringify(inner);
    if (key === this._key) return;
    this._key = key;
    this._build(inner, key);
  }

  async _build(inner, key) {
    if (!inner) {
      this._card = undefined;
      this.replaceChildren(this._warning());
      return;
    }
    const helpers = await window.loadCardHelpers();
    // A newer configuration arrived while the helpers were loading.
    if (key !== this._key) return;
    const card = helpers.createCardElement(inner);
    card.hass = this._hass;
    this._card = card;
    this.replaceChildren(card);
  }

  _warning() {
    const card = document.createElement("ha-card");
    const text = document.createElement("div");
    text.style.padding = "16px";
    text.style.color = "var(--secondary-text-color)";
    text.textContent =
      this._hass.localize?.("ui.panel.lovelace.warning.entity_not_found", {
        entity: this._config.device_id || "Storz & Bickel",
      }) || "Choose a Storz & Bickel device";
    card.append(text);
    return card;
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? 7;
  }

  getGridOptions() {
    return (
      this._card?.getGridOptions?.() ?? {
        columns: 12,
        rows: 7,
        min_columns: 6,
        min_rows: 4,
      }
    );
  }

  static getStubConfig(hass) {
    return stubConfig(hass);
  }

  // Home Assistant builds the editor from this: one device picker, limited
  // to this integration's devices, and an optional name.
  static getConfigForm() {
    return {
      schema: [
        {
          name: "device_id",
          required: true,
          selector: { device: { integration: DOMAIN } },
        },
        { name: "name", selector: { text: {} } },
      ],
      computeLabel: (schema, localize) =>
        schema.name === "device_id"
          ? localize("ui.panel.lovelace.editor.card.generic.device") ||
            localize("ui.common.device") ||
            "Device"
          : localize("ui.panel.lovelace.editor.card.generic.name") || "Name",
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
        margin: 0;
        padding: 0 12px;
        border: none;
        border-radius: var(--feature-border-radius, 12px);
        background: color-mix(in srgb, var(--tone) 20%, transparent);
        color: var(--primary-text-color);
        font: inherit;
        font-size: var(--ha-font-size-m, 14px);
        cursor: pointer;
        -webkit-tap-highlight-color: transparent;
      }
      .item:focus-visible {
        outline: 2px solid var(--tone);
        outline-offset: 2px;
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
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  set context(context) {
    this._context = context;
    this._update();
  }

  _update() {
    if (!this._hass || !this._context?.entity_id) return;
    const deviceId = deviceOf(this._hass, this._context.entity_id);
    const resolved = resolveDevice(this._hass, deviceId);
    const items = resolved ? statusItems(this._hass, resolved) : [];

    // The icons need the newest state objects even when nothing else changed.
    const key = JSON.stringify(items);
    if (key === this._key) {
      for (const icon of this._row.querySelectorAll("ha-state-icon")) {
        icon.hass = this._hass;
        icon.stateObj = this._hass.states[icon.dataset.entity];
      }
      return;
    }
    this._key = key;
    this._row.replaceChildren(...items.map((item) => this._item(item)));
  }

  _item(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "item";
    button.style.setProperty("--tone", TONES[item.tone]);
    button.title = item.label;

    const icon = document.createElement("ha-state-icon");
    icon.dataset.entity = item.entity;
    icon.hass = this._hass;
    icon.stateObj = this._hass.states[item.entity];

    const label = document.createElement("span");
    label.className = "label";
    label.textContent = item.label;

    button.append(icon, label);
    button.addEventListener("click", (event) => {
      // Otherwise the card underneath opens its own dialog as well.
      event.stopPropagation();
      if (item.press) {
        this._hass.callService("button", "press", { entity_id: item.entity });
        return;
      }
      this.dispatchEvent(
        new CustomEvent("hass-more-info", {
          detail: { entityId: item.entity },
          bubbles: true,
          composed: true,
        })
      );
    });
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
      "Temperature, heat and air for a Volcano Hybrid, Crafty, Venty or Veazy.",
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
