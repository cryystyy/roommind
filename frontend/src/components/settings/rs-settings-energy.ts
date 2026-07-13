/**
 * rs-settings-energy – Economic MPC (price/COP/PV) and humidity-aware
 * cooling (feels-like targets, dew-point guard) settings.
 */
import { html, css } from "lit";
import { RsSettingsBase } from "./rs-settings-base";
import { customElement, property } from "lit/decorators.js";
import type { HomeAssistant, HassEntity } from "../../types";
import { localize } from "../../utils/localize";
import "../shared/rs-toggle-row";

@customElement("rs-settings-energy")
export class RsSettingsEnergy extends RsSettingsBase {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ type: String }) public priceEntity = "";
  @property({ type: String }) public gridExportEntity = "";
  @property({ type: Number }) public pvExportThresholdW = 300;
  @property({ type: Number }) public hpCopAtMinus7 = 0;
  @property({ type: Number }) public hpCopAtPlus7 = 0;
  @property({ type: Boolean }) public feelsLikeEnabled = false;
  @property({ type: Boolean }) public dewpointGuardEnabled = true;
  @property({ type: Number }) public dewpointMargin = 2.0;

  private _filterPower = (entity: HassEntity): boolean => {
    return entity.attributes?.device_class === "power";
  };

  render() {
    const l = this.hass.language;
    return html`
      <div class="settings-section first">
        <ha-entity-picker
          .hass=${this.hass}
          .value=${this.priceEntity}
          .includeDomains=${["sensor"]}
          .label=${localize("energy.price_entity", l)}
          allow-custom-entity
          @value-changed=${(e: CustomEvent) => {
            const v = (e.detail?.value as string) ?? "";
            if (v !== this.priceEntity) this._fire("priceEntity", v);
          }}
        ></ha-entity-picker>
        <span class="field-hint">${localize("energy.price_entity_hint", l)}</span>
      </div>

      <div class="settings-section">
        <div class="field-grid">
          <div>
            <ha-entity-picker
              .hass=${this.hass}
              .value=${this.gridExportEntity}
              .includeDomains=${["sensor"]}
              .entityFilter=${this._filterPower}
              .label=${localize("energy.grid_export_entity", l)}
              allow-custom-entity
              @value-changed=${(e: CustomEvent) => {
                const v = (e.detail?.value as string) ?? "";
                if (v !== this.gridExportEntity) this._fire("gridExportEntity", v);
              }}
            ></ha-entity-picker>
            <span class="field-hint">${localize("energy.grid_export_hint", l)}</span>
          </div>
          <div>
            <ha-textfield
              .value=${String(this.pvExportThresholdW)}
              .label=${localize("energy.pv_threshold", l)}
              suffix="W"
              type="number"
              step="50"
              min="0"
              max="20000"
              @change=${(e: Event) => {
                const v = parseFloat((e.target as HTMLInputElement).value);
                if (!isNaN(v)) this._fire("pvExportThresholdW", v);
              }}
            ></ha-textfield>
          </div>
        </div>
      </div>

      <div class="settings-section">
        <p class="hint">${localize("energy.cop_hint", l)}</p>
        <div class="field-grid">
          <ha-textfield
            .value=${String(this.hpCopAtMinus7)}
            .label=${localize("energy.cop_minus7", l)}
            type="number"
            step="0.1"
            min="0"
            max="8"
            @change=${(e: Event) => {
              const v = parseFloat((e.target as HTMLInputElement).value);
              if (!isNaN(v)) this._fire("hpCopAtMinus7", v);
            }}
          ></ha-textfield>
          <ha-textfield
            .value=${String(this.hpCopAtPlus7)}
            .label=${localize("energy.cop_plus7", l)}
            type="number"
            step="0.1"
            min="0"
            max="8"
            @change=${(e: Event) => {
              const v = parseFloat((e.target as HTMLInputElement).value);
              if (!isNaN(v)) this._fire("hpCopAtPlus7", v);
            }}
          ></ha-textfield>
        </div>
      </div>

      <div class="settings-section">
        <rs-toggle-row
          .label=${localize("energy.feels_like", l)}
          .hint=${localize("energy.feels_like_hint", l)}
          .checked=${this.feelsLikeEnabled}
          @toggle-changed=${(e: CustomEvent) => this._fire("feelsLikeEnabled", e.detail)}
        ></rs-toggle-row>
      </div>

      <div class="settings-section">
        <rs-toggle-row
          .label=${localize("energy.dewpoint_guard", l)}
          .hint=${localize("energy.dewpoint_guard_hint", l)}
          .checked=${this.dewpointGuardEnabled}
          @toggle-changed=${(e: CustomEvent) => this._fire("dewpointGuardEnabled", e.detail)}
        ></rs-toggle-row>
        ${this.dewpointGuardEnabled
          ? html`
              <ha-textfield
                class="margin-field"
                .value=${String(this.dewpointMargin)}
                .label=${localize("energy.dewpoint_margin", l)}
                suffix="°C"
                type="number"
                step="0.5"
                min="0.5"
                max="5"
                @change=${(e: Event) => {
                  const v = parseFloat((e.target as HTMLInputElement).value);
                  if (!isNaN(v)) this._fire("dewpointMargin", v);
                }}
              ></ha-textfield>
            `
          : ""}
      </div>
    `;
  }

  static styles = [
    RsSettingsBase.settingsBaseStyles,
    css`
      .field-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 16px;
        align-items: start;
      }
      .margin-field {
        margin-top: 12px;
        max-width: 220px;
      }
      @media (max-width: 600px) {
        .field-grid {
          grid-template-columns: 1fr;
        }
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "rs-settings-energy": RsSettingsEnergy;
  }
}
