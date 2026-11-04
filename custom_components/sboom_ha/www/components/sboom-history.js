/** Personal successful launches, persisted by Home Assistant. */
import { LitElement, html, css, nothing } from "../lit-base.js";

class SboomHistory extends LitElement {
  static properties = {
    hass: { type: Object }, entryId: { type: String }, refreshToken: { type: Number },
    _items: { state: true }, _order: { state: true }, _busy: { state: true },
    _error: { state: true }, _confirmClear: { state: true },
  };

  constructor() {
    super();
    this.hass = null;
    this.entryId = null;
    this.refreshToken = 0;
    this._items = [];
    this._order = "recent";
    this._busy = false;
    this._error = "";
    this._confirmClear = false;
    this._requestId = 0;
    this._loadedUser = null;
  }

  connectedCallback() {
    super.connectedCallback();
    if (this.hass?.user?.id) this._load();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._requestId += 1;
    this._busy = false;
  }

  updated(changed) {
    const user = this.hass?.user?.id;
    if (!user) {
      if (this._loadedUser !== null || this._items.length || this._busy) {
        this._requestId += 1;
        this._loadedUser = null;
        this._items = [];
        this._busy = false;
      }
      return;
    }
    if (user !== this._loadedUser || changed.has("entryId") || changed.has("refreshToken")) {
      if (user !== this._loadedUser) this._items = [];
      this._load();
    }
  }

  async _load() {
    if (!this.hass?.user?.id) return;
    const request = ++this._requestId;
    const user = this.hass.user.id;
    this._loadedUser = user;
    this._confirmClear = false;
    this._busy = true;
    this._error = "";
    try {
      const result = await this.hass.callWS({ type: "sboom/history", order: this._order });
      if (request !== this._requestId || user !== this.hass?.user?.id) return;
      this._items = Array.isArray(result?.items) ? result.items : [];
    } catch (error) {
      if (request === this._requestId && user === this.hass?.user?.id) this._error = error.message || String(error);
    } finally {
      if (request === this._requestId && user === this.hass?.user?.id) this._busy = false;
    }
  }

  async _clear() {
    if (this._busy || !this.hass?.user?.id) return;
    if (!this._confirmClear) { this._confirmClear = true; return; }
    const request = ++this._requestId;
    const user = this.hass.user.id;
    this._busy = true;
    this._error = "";
    try {
      await this.hass.callWS({ type: "sboom/history_clear" });
      if (request !== this._requestId || user !== this.hass?.user?.id) return;
      this._items = [];
      this._confirmClear = false;
    } catch (error) {
      if (request === this._requestId && user === this.hass?.user?.id) this._error = error.message || String(error);
    } finally {
      if (request === this._requestId && user === this.hass?.user?.id) this._busy = false;
    }
  }

  _play(item) {
    if (this._busy) return;
    this.dispatchEvent(new CustomEvent("history-play", { detail: item, bubbles: true, composed: true }));
  }

  _sort(order) {
    this._order = order;
    this._load();
  }

  render() {
    return html`<div class="toolbar">
      <div class="orders" role="group" aria-label="Порядок истории">
        <button aria-pressed=${String(this._order === "recent")} ?disabled=${this._busy} @click=${() => this._sort("recent")}>Недавние</button>
        <button aria-pressed=${String(this._order === "frequent")} ?disabled=${this._busy} @click=${() => this._sort("frequent")}>Частые</button>
      </div>
      ${this._items.length ? html`<button class="clear" ?disabled=${this._busy} @click=${this._clear}>
        ${this._confirmClear ? "Подтвердить очистку" : "Очистить историю"}</button>` : nothing}
      ${this._confirmClear ? html`<button @click=${() => { this._confirmClear = false; }}>Отмена</button>` : nothing}
    </div>
    ${this._error ? html`<p class="error" role="alert">${this._error} <button @click=${this._load}>Повторить</button></p>` : nothing}
    ${this._busy ? html`<p role="status">Загрузка истории…</p>` : nothing}
    ${!this._items.length && !this._busy && !this._error ? html`<p class="empty">Здесь появятся ваши запуски из каталога. Найдите музыку и нажмите «Слушать».</p>` : nothing}
    <ul>${this._items.map((item) => html`<li>
      <button class="item" ?disabled=${this._busy || !this.entryId} @click=${() => this._play(item)} aria-label=${`Слушать ${item.title || item.id}`}>
        ${item.cover_url ? html`<img src=${item.cover_url} alt="" loading="lazy" />` : html`<span class="art" aria-hidden="true">♪</span>`}
        <span class="meta"><strong>${item.title || item.id}</strong><span>${item.subtitle || item.kind}</span></span>
        ${this._order === "frequent" ? html`<span class="count">${item.count}×</span>` : nothing}
        <span class="play" aria-hidden="true">▶</span>
      </button>
    </li>`)}</ul>`;
  }

  static styles = css`
    :host { display: block; color: var(--sb-ink); font: inherit; }
    .toolbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 16px; }
    .orders { display: flex; gap: 4px; margin-right: auto; }
    button { font: inherit; font-size: 13px; color: var(--sb-ink-dim); background: transparent; border: 1px solid var(--sb-line); border-radius: var(--sb-radius-sm); padding: 8px 10px; cursor: pointer; }
    button[aria-pressed="true"] { color: var(--sb-ink); background: var(--sb-elev-2); border-color: var(--sb-accent); }
    button:focus-visible { outline: 2px solid var(--sb-accent); outline-offset: 2px; }
    button:disabled { opacity: .5; cursor: default; }
    .clear { font-size: 12px; }
    p { font-size: 13px; line-height: 1.6; color: var(--sb-ink-dim); }
    .error { color: var(--error-color, #db4437); overflow-wrap: anywhere; }
    .empty { padding: 20px 0; }
    ul { list-style: none; margin: 0; padding: 0; max-height: 560px; overflow-y: auto; }
    li + li { border-top: 1px solid var(--sb-line); }
    .item { display: flex; width: 100%; align-items: center; gap: 12px; text-align: left; border: 0; padding: 10px 4px; }
    .item:hover { background: var(--sb-elev-2); }
    img, .art { width: 42px; height: 42px; border-radius: 6px; object-fit: cover; flex-shrink: 0; }
    .art { display: grid; place-items: center; background: var(--sb-elev-2); font-size: 23px; }
    .meta { min-width: 0; flex: 1; }
    strong, .meta > span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    strong { font-size: 14px; color: var(--sb-ink); font-weight: 500; }
    .meta > span { font-size: 12px; margin-top: 4px; }
    .count { font-size: 12px; }
    .play { color: var(--sb-accent); font-size: 12px; padding: 8px; }
  `;
}

if (!customElements.get("sboom-history")) customElements.define("sboom-history", SboomHistory);
