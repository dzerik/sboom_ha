/** Bluetooth actions on the selected speaker, over the existing HA connection. */
import { LitElement, html, css, nothing } from "../lit-base.js";

class SboomBluetooth extends LitElement {
  static properties = {
    hass: { type: Object }, entryId: { type: String }, connected: { type: Boolean },
    _open: { state: true }, _busy: { state: true }, _paired: { state: true },
    _scanned: { state: true }, _error: { state: true }, _notice: { state: true },
  };

  constructor() {
    super();
    this.hass = null;
    this.entryId = null;
    this.connected = false;
    this._open = false;
    this._busy = false;
    this._paired = [];
    this._scanned = [];
    this._error = "";
    this._notice = "";
    this._epoch = 0;
  }

  connectedCallback() {
    super.connectedCallback();
    if (this._open) this._request("status");
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._epoch += 1;
    this._busy = false;
  }

  updated(changed) {
    if (changed.has("entryId") || (changed.has("connected") && !this.connected)) {
      this._epoch += 1;
      this._paired = [];
      this._scanned = [];
      this._error = "";
      this._notice = "";
      this._busy = false;
    }
    if (this._open && this.connected && this.entryId &&
        (changed.has("entryId") || changed.has("connected"))) {
      this._request("status");
    }
  }

  _toggle(event) {
    if (this._open === event.target.open) return;
    this._open = event.target.open;
    if (this._open) this._request("status");
  }

  async _request(action, mac) {
    if (!this.hass || !this.entryId || !this.connected || this._busy) return;
    const epoch = this._epoch;
    const entry = this.entryId;
    this._busy = true;
    this._busyAction = action;
    this._error = "";
    this._notice = "";
    try {
      const result = await this.hass.callWS({
        type: "sboom/bluetooth", action, entry_id: entry,
        ...(mac ? { mac_address: mac } : {}),
      });
      if (epoch !== this._epoch || entry !== this.entryId) return;
      this._paired = Array.isArray(result?.paired) ? result.paired : [];
      this._scanned = Array.isArray(result?.scanned) ? result.scanned : [];
      if (action === "pair") {
        this._notice = "Команда сопряжения отправлена. Найдите колонку в настройках Bluetooth телефона.";
      }
    } catch (error) {
      if (epoch === this._epoch && entry === this.entryId) {
        this._error = error.message || String(error);
      }
    } finally {
      if (epoch === this._epoch && entry === this.entryId) this._busy = false;
    }
  }

  _device(device, paired) {
    return html`<li>
      <div class="device">
        <strong>${device.name || "Без имени"}</strong>
        <span>${device.mac}${paired ? (device.connected ? " · Подключено" : " · Не подключено") :
          (Number.isFinite(device.rssi) ? ` · RSSI ${device.rssi}` : "")}</span>
      </div>
      <div class="actions">
        <button ?disabled=${this._busy || !this.connected}
          @click=${() => this._request(device.connected ? "disconnect" : "connect", device.mac)}>
          ${device.connected ? "Отключить" : "Подключить"}
        </button>
        ${paired ? html`<button class="subtle" ?disabled=${this._busy || !this.connected}
          @click=${() => this._request("remove", device.mac)}>Забыть</button>` : nothing}
      </div>
    </li>`;
  }

  render() {
    return html`<details ?open=${this._open} @toggle=${this._toggle}>
      <summary>Bluetooth <span>Устройства и сопряжение</span></summary>
      <div class="body">
        ${!this.connected ? html`<p role="status">Подключите колонку, чтобы управлять Bluetooth.</p>` : nothing}
        <div class="toolbar">
          <button ?disabled=${this._busy || !this.connected} @click=${() => this._request("pair")}>Начать сопряжение</button>
          <button ?disabled=${this._busy || !this.connected} @click=${() => this._request("scan")}>Найти устройства</button>
          <button class="subtle" ?disabled=${this._busy || !this.connected} @click=${() => this._request("status")}>Обновить</button>
        </div>
        ${this._busy ? html`<p role="status">${this._busyAction === "scan" ? "Поиск устройств…" : "Выполняю команду…"}</p>` : nothing}
        ${this._error ? html`<p class="error" role="alert">${this._error}</p>` : nothing}
        ${this._notice ? html`<p role="status">${this._notice}</p>` : nothing}
        <h3>Спаренные устройства</h3>
        ${this._paired.length ? html`<ul>${this._paired.map((d) => this._device(d, true))}</ul>` :
          html`<p>Нет спаренных устройств. Начните сопряжение с телефона.</p>`}
        <h3>Рядом с колонкой</h3>
        ${this._scanned.length ? html`<ul>${this._scanned.map((d) => this._device(d, false))}</ul>` :
          html`<p>Нажмите «Найти устройства». На устройстве должен быть доступен режим сопряжения.</p>`}
      </div>
    </details>`;
  }

  static styles = css`
    :host { display: block; color: var(--sb-ink); font: inherit; }
    details { background: var(--sb-elev); border: 1px solid var(--sb-line); border-radius: var(--sb-radius); }
    summary { cursor: pointer; padding: 18px; font-weight: 600; }
    summary span { display: block; padding: 5px 0 0 16px; color: var(--sb-ink-dim); font-size: 12px; font-weight: 400; }
    .body { padding: 0 18px 18px; }
    .toolbar, .actions { display: flex; gap: 8px; flex-wrap: wrap; }
    button { font: inherit; font-size: 13px; padding: 9px 12px; border: 1px solid var(--sb-line); border-radius: var(--sb-radius-sm); background: var(--sb-elev-2); color: var(--sb-ink); cursor: pointer; }
    button:hover { border-color: var(--sb-accent); }
    button:focus-visible, summary:focus-visible { outline: 2px solid var(--sb-accent); outline-offset: 3px; }
    button:disabled { opacity: .45; cursor: default; }
    .subtle { background: transparent; color: var(--sb-ink-dim); }
    h3 { font-size: 13px; margin: 22px 0 8px; }
    p { font-size: 13px; line-height: 1.5; color: var(--sb-ink-dim); }
    .error { color: var(--error-color, #db4437); overflow-wrap: anywhere; }
    ul { list-style: none; margin: 0; padding: 0; }
    li { display: flex; gap: 12px; flex-wrap: wrap; justify-content: space-between; align-items: center; padding: 12px 0; border-bottom: 1px solid var(--sb-line); }
    li:last-child { border: 0; }
    .device { min-width: 0; flex: 1 1 140px; }
    strong { display: block; font-size: 14px; overflow-wrap: anywhere; }
    .device span { display: block; color: var(--sb-ink-dim); font-size: 11px; margin-top: 4px; overflow-wrap: anywhere; }
  `;
}

if (!customElements.get("sboom-bluetooth")) customElements.define("sboom-bluetooth", SboomBluetooth);
