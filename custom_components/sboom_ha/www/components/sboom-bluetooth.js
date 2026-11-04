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
      <span class="device-icon ${device.connected ? "active" : ""}" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path d="m7 7 10 10-5 4V3l5 4L7 17"/></svg>
      </span>
      <div class="device">
        <strong title=${device.name || "Без имени"}>${device.name || "Без имени"}</strong>
        <div class="device-meta">
          ${paired ? html`<span class=${device.connected ? "connected" : ""}>
            ${device.connected ? "Подключено" : "Не подключено"}
          </span>` : nothing}
          <span class="address">${device.mac}</span>
          ${!paired && Number.isFinite(device.rssi) ? html`<span>RSSI ${device.rssi}</span>` : nothing}
        </div>
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
      <summary>
        <span class="title">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 7 10 10-5 4V3l5 4L7 17"/></svg>
          Bluetooth
        </span>
        <svg class="chevron" viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      </summary>
      <div class="body" aria-busy=${this._busy ? "true" : "false"}>
        ${!this.connected ? html`<p role="status">Подключите колонку, чтобы управлять Bluetooth.</p>` : nothing}
        <div class="toolbar">
          <button class="primary" ?disabled=${this._busy || !this.connected} @click=${() => this._request("pair")}>Начать сопряжение</button>
          <button ?disabled=${this._busy || !this.connected} @click=${() => this._request("scan")}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
            Найти устройства
          </button>
        </div>
        ${this._busy ? html`<p role="status">${this._busyAction === "scan" ? "Поиск устройств…" : "Выполняю команду…"}</p>` : nothing}
        ${this._error ? html`<p class="error" role="alert">${this._error}</p>` : nothing}
        ${this._notice ? html`<p role="status">${this._notice}</p>` : nothing}
        <div class="list-heading">
          <h3>Сохранённые устройства</h3>
          <button class="refresh" title="Обновить устройства" aria-label="Обновить устройства"
            ?disabled=${this._busy || !this.connected} @click=${() => this._request("status")}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M20 12a8 8 0 1 0-2 5M20 7l-2-2"/></svg>
          </button>
        </div>
        ${this._paired.length ? html`<ul>${this._paired.map((d) => this._device(d, true))}</ul>` :
          html`<p class="empty">Пока нет устройств. Начните сопряжение с телефона.</p>`}
        <h3>Рядом с колонкой</h3>
        ${this._scanned.length ? html`<ul>${this._scanned.map((d) => this._device(d, false))}</ul>` :
          html`<p class="empty">Нажмите «Найти устройства». На устройстве должен быть доступен режим сопряжения.</p>`}
      </div>
    </details>`;
  }

  static styles = css`
    :host {
      display: block;
      color: var(--sb-ink);
      font-family: var(--sb-disp);
      container-type: inline-size;
    }
    * { box-sizing: border-box; }
    details { background: none; border: none; }
    summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 44px;
      padding: 10px 4px;
      border-radius: var(--sb-radius-sm);
      list-style: none;
      cursor: pointer;
      user-select: none;
    }
    summary::-webkit-details-marker { display: none; }
    summary::marker { content: ""; }
    .title {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      font-weight: 600;
      letter-spacing: .4px;
      text-transform: uppercase;
      color: var(--sb-ink-dim);
    }
    svg {
      display: block;
      width: 18px;
      height: 18px;
      flex: none;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.7;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .chevron {
      width: 16px;
      height: 16px;
      color: var(--sb-ink-faint);
      transition: transform .2s ease;
    }
    details[open] .chevron { transform: rotate(180deg); }
    summary:hover .title, summary:hover .chevron { color: var(--sb-ink); }
    .body { padding: 4px 4px 14px; }
    .toolbar, .actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 36px;
      max-width: 100%;
      padding: 8px 14px;
      border: 1px solid var(--sb-line);
      border-radius: 999px;
      background: transparent;
      color: var(--sb-ink-dim);
      font: inherit;
      font-size: 12px;
      line-height: 1.4;
      cursor: pointer;
      transition: background .15s ease, color .15s ease, border-color .15s ease;
    }
    button svg { width: 15px; height: 15px; }
    button:hover:not(:disabled) {
      color: var(--sb-ink);
      border-color: var(--sb-ink-faint);
      background: var(--sb-elev-2);
    }
    .primary {
      color: var(--text-primary-color, #fff);
      background: var(--sb-accent);
      border-color: var(--sb-accent);
      font-weight: 600;
    }
    .primary:hover:not(:disabled) {
      color: var(--text-primary-color, #fff);
      background: color-mix(in srgb, var(--sb-accent) 85%, var(--sb-ink));
      border-color: transparent;
    }
    button:focus-visible, summary:focus-visible { outline: 2px solid var(--sb-accent); outline-offset: 3px; }
    button:disabled { opacity: .45; cursor: default; }
    .subtle { border-color: transparent; color: var(--sb-ink-faint); }
    .list-heading { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 22px 0 8px; }
    .list-heading h3 { margin: 0; }
    .refresh { width: 32px; min-height: 32px; flex: none; padding: 7px; border-radius: var(--sb-radius-sm); color: var(--sb-ink-faint); }
    h3 {
      margin: 22px 0 8px;
      color: var(--sb-ink-faint);
      font-size: 11px;
      font-weight: 600;
      letter-spacing: .14em;
      text-transform: uppercase;
    }
    p { margin: 10px 0; font-size: 13px; line-height: 1.5; color: var(--sb-ink-dim); }
    .empty { color: var(--sb-ink-faint); }
    .error { color: var(--error-color, #db4437); overflow-wrap: anywhere; }
    ul { list-style: none; margin: 0; padding: 0; }
    li {
      display: grid;
      grid-template-columns: 36px minmax(0, 1fr);
      align-items: center;
      gap: 10px 12px;
      padding: 12px 0;
      border-bottom: 1px solid var(--sb-line);
    }
    li:last-child { border-bottom: 0; }
    .device-icon {
      display: flex;
      align-items: center;
      justify-content: center;
      width: 36px;
      height: 36px;
      border-radius: var(--sb-radius-sm);
      background: var(--sb-elev-2);
      color: var(--sb-ink-faint);
    }
    .device-icon.active { color: var(--sb-accent); background: var(--sb-glow-soft); }
    .device { min-width: 0; }
    strong { display: block; font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .device-meta { display: flex; flex-wrap: wrap; gap: 4px 8px; margin-top: 4px; color: var(--sb-ink-faint); font-size: 11px; line-height: 1.5; }
    .device-meta span { overflow-wrap: anywhere; min-width: 0; }
    .device-meta .connected { color: var(--sb-accent); }
    .address { font-family: var(--sb-mono); }
    .actions { grid-column: 2; }
    @container (min-width: 480px) {
      li { grid-template-columns: 36px minmax(0, 1fr) auto; }
      .actions { grid-column: 3; grid-row: 1; }
    }
    @media (prefers-reduced-motion: reduce) {
      .chevron, button { transition: none; }
    }
  `;
}

if (!customElements.get("sboom-bluetooth")) customElements.define("sboom-bluetooth", SboomBluetooth);
