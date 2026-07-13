/**
 * SBoom — main panel (host).
 *
 * Премиум-плеер для колонки SberBoom. Тёмная самодостаточная «сцена»: hero
 * now-playing слева, справа переключаемые вкладки Очередь / Поиск. Сигнатура —
 * ambient-glow за hero, производный от доминирующего цвета обложки (эхо
 * LED-кольца колонки). См. DESIGN_SPEC.md.
 *
 * Host владеет: опросом sboom/state (5s), извлечением цвета обложки → CSS-var
 * --sb-glow, раскладкой/вкладками, версией (this.panel.config.version), и
 * форвардингом @seek/@toast от дочерних компонентов.
 */

const _v = new URL(import.meta.url).searchParams.get("v") || "";
const _q = _v ? `?v=${_v}` : "";
await Promise.all([
  import(`./components/sboom-toast.js${_q}`),
  import(`./components/sboom-nowplaying.js${_q}`),
  import(`./components/sboom-controls.js${_q}`),
  import(`./components/sboom-track-row.js${_q}`),
  import(`./components/sboom-tile.js${_q}`),
  import(`./components/sboom-browse.js${_q}`),
]);

import { LitElement, html, css, nothing } from "./lit-base.js";

const DEFAULT_GLOW = "#7C5CFF";

class SboomPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean },
      panel: { type: Object },
      _state: { type: Object },
      _error: { type: String },
      _glow: { type: String },
      _devices: { type: Array },
      _entryId: { type: String },
      _picker: { type: Boolean },
      _idle: { type: Boolean },
    };
  }

  constructor() {
    super();
    this._state = null;
    this._error = "";
    this._glow = DEFAULT_GLOW;
    this._devices = [];
    this._entryId = null;
    this._picker = false; // открыт ли дропдаун выбора колонки
    this._devicesLoaded = false;
    this._unsub = null; // отписка от push-подписки
    this._subscribing = false;
    this._autoRefresh = null; // fallback-поллинг, если подписка недоступна
    this._lastCover = null;
    this._idle = false; // панель приглушена после простоя
    this._idleTimer = null;
    this._wake = this._wake.bind(this);
  }

  connectedCallback() {
    super.connectedCallback();
    this._ensureFeed();
    this._wake();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this._teardownFeed();
    clearTimeout(this._idleTimer);
  }

  // Активность мыши/касания «будит» панель; через 10с простоя — приглушаем
  // (контролы+метаданные становятся прозрачнее, обложка видна лучше).
  _wake() {
    if (this._idle) this._idle = false;
    clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => {
      this._idle = true;
    }, 10000);
  }

  updated(changed) {
    if (changed.has("hass") && this.hass) this._ensureFeed();
  }

  get _version() {
    return this.panel?.config?.version || this._state?.version || "";
  }

  // ── источник состояния: push-подписка на координатор (мгновенно) ────────
  // Заменяет 5-сек поллинг: sboom/subscribe шлёт свежее состояние на каждом
  // push от колонки. Колонок может быть несколько — адресуем по entry_id.
  async _ensureFeed() {
    if (!this.hass || this._unsub || this._autoRefresh || this._subscribing) {
      return;
    }
    this._subscribing = true;
    try {
      await this._loadDevicesOnce();
      this._unsub = await this.hass.connection.subscribeMessage(
        (msg) => this._applyState(msg),
        { type: "sboom/subscribe", entry_id: this._entryId }
      );
    } catch {
      // подписка недоступна — деградируем до поллинга
      this._autoRefresh = setInterval(() => this._fetchState(), 5000);
      this._fetchState();
    } finally {
      this._subscribing = false;
    }
  }

  async _loadDevicesOnce() {
    if (this._devicesLoaded) return;
    this._devicesLoaded = true;
    try {
      const res = await this.hass.callWS({ type: "sboom/devices" });
      this._devices = res?.devices || [];
    } catch {
      this._devices = [];
    }
    if (!this._entryId || !this._devices.some((d) => d.entry_id === this._entryId)) {
      this._entryId = this._devices[0]?.entry_id || null;
    }
  }

  _togglePicker(e) {
    e?.stopPropagation();
    this._picker = !this._picker;
    if (this._picker) {
      const close = () => {
        this._picker = false;
        window.removeEventListener("click", close);
      };
      setTimeout(() => window.addEventListener("click", close), 0);
    }
  }

  _selectDevice(entryId) {
    this._picker = false;
    if (entryId === this._entryId) return;
    this._entryId = entryId;
    this._state = null;
    this._glow = DEFAULT_GLOW;
    this._lastCover = null;
    this._teardownFeed();
    this._ensureFeed();
  }

  get _deviceName() {
    const d = this._devices.find((x) => x.entry_id === this._entryId);
    return d?.name || "SberBoom";
  }

  _teardownFeed() {
    if (this._unsub) {
      this._unsub();
      this._unsub = null;
    }
    if (this._autoRefresh) {
      clearInterval(this._autoRefresh);
      this._autoRefresh = null;
    }
  }

  _applyState(state) {
    this._state = state;
    this._error = "";
    this._syncGlow(state?.track?.cover_url || null);
  }

  async _fetchState() {
    if (!this.hass) return;
    try {
      this._applyState(
        await this.hass.callWS({ type: "sboom/state", entry_id: this._entryId })
      );
    } catch (e) {
      this._error = e.message || String(e);
    }
  }

  // ── ambient glow: доминирующий цвет обложки (считается на сервере) ──────
  // CDN Звука не отдаёт CORS → клиентский canvas невозможен. Цвет берём через
  // WS sboom/cover_color (Pillow на бэкенде, кеш по URL).
  async _syncGlow(coverUrl) {
    if (coverUrl === this._lastCover) return;
    this._lastCover = coverUrl;
    if (!coverUrl || !this.hass) {
      this._glow = DEFAULT_GLOW;
      return;
    }
    try {
      const resp = await this.hass.callWS({
        type: "sboom/cover_color",
        url: coverUrl,
      });
      // защита от гонки: обложка могла смениться, пока считался цвет
      if (coverUrl === this._lastCover) {
        this._glow = resp?.color || DEFAULT_GLOW;
      }
    } catch {
      this._glow = DEFAULT_GLOW;
    }
  }

  // ── события дочерних компонентов ───────────────────────────────────────
  _onToast(e) {
    const toast = this.shadowRoot.querySelector("sboom-toast");
    if (toast) toast.show(e.detail.message, e.detail.type);
  }

  async _onSeek(e) {
    if (!this.hass) return;
    try {
      await this.hass.callWS({
        type: "sboom/command",
        action: "seek",
        value: Math.round(e.detail.value),
        entry_id: this._entryId,
      });
    } catch (err) {
      this._onToast({ detail: { message: `Ошибка перемотки: ${err.message || err}`, type: "error" } });
    }
  }

  static get styles() {
    return css`
      :host {
        /* Палитра — из темы Home Assistant (адаптируется к светлой/тёмной/
           кастомной), а не жёсткий «AI-дефолт». Люкс — в исполнении. */
        --sb-stage: var(--primary-background-color, #111418);
        --sb-elev: var(--card-background-color, var(--ha-card-background, #1c1f26));
        --sb-elev-2: var(
          --secondary-background-color,
          var(--state-icon-hover-color, #2a2f3a)
        );
        --sb-line: var(--divider-color, rgba(127, 127, 127, 0.2));
        --sb-ink: var(--primary-text-color, #e8eaed);
        --sb-ink-dim: var(--secondary-text-color, rgba(232, 234, 237, 0.65));
        --sb-ink-faint: var(--disabled-text-color, rgba(232, 234, 237, 0.42));
        /* акцент — фирменный цвет темы HA; glow (ambient) — из обложки */
        --sb-accent: var(--primary-color, #03a9f4);
        --sb-glow: var(--primary-color, #03a9f4);
        --sb-glow-soft: color-mix(in srgb, var(--sb-glow) 20%, transparent);
        --sb-like: var(--primary-color, #ff5c8a);
        --sb-radius: var(--ha-card-border-radius, 12px);
        --sb-radius-sm: 8px;
        --sb-gap: 16px;
        --sb-disp: "SF Pro Display", "Inter", system-ui, sans-serif;
        --sb-mono: ui-monospace, "SF Mono", "Roboto Mono", monospace;

        display: block;
        min-height: 100%;
        box-sizing: border-box;
        color: var(--sb-ink);
        background:
          radial-gradient(120% 80% at 18% 0%, var(--sb-glow-soft), transparent 60%),
          var(--sb-stage);
        font-family: system-ui, "Segoe UI", Roboto, sans-serif;
        transition: --sb-glow 0.6s ease;
      }

      .wrap {
        display: grid;
        grid-template-columns: minmax(0, 1.05fr) minmax(0, 1fr);
        gap: 24px;
        max-width: 1120px;
        margin: 0 auto;
        padding: 22px 22px 40px;
        box-sizing: border-box;
      }

      .left {
        min-width: 0;
        position: sticky;
        top: 22px;
        align-self: start;
      }
      .right {
        min-width: 0;
        display: flex;
        flex-direction: column;
      }

      /* ── Immersive player card: обложка-фон + фрост-стекло снизу ── */
      .player {
        position: relative;
        border-radius: var(--sb-radius);
        overflow: hidden;
        aspect-ratio: 1 / 1; /* ближе к квадрату */
        min-height: 520px;
        display: flex;
        isolation: isolate;
        box-shadow:
          0 26px 64px -24px rgba(0, 0, 0, 0.65),
          inset 0 0 0 1px rgba(255, 255, 255, 0.06);
      }
      .art {
        position: absolute;
        inset: 0;
        z-index: 0;
        background:
          radial-gradient(
            120% 90% at 30% 12%,
            var(--sb-glow-soft),
            transparent 60%
          ),
          var(--sb-elev);
      }
      .art img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
      }
      .veil {
        position: absolute;
        inset: 0;
        z-index: 1;
        pointer-events: none;
        background: linear-gradient(
          to bottom,
          rgba(6, 6, 10, 0.5),
          transparent 24%
        );
      }
      .head {
        position: absolute;
        inset: 0 0 auto 0;
        z-index: 3;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 14px 16px;
      }
      .picker {
        position: relative;
        min-width: 0;
      }
      .brand {
        display: inline-flex;
        align-items: center;
        gap: 9px;
        max-width: 60vw;
        border: none;
        background: rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(8px);
        color: #fff;
        font: inherit;
        font-weight: 600;
        font-size: 14px;
        padding: 7px 12px;
        border-radius: 999px;
        cursor: default;
      }
      .brand.switch {
        cursor: pointer;
      }
      .brand.switch:hover {
        background: rgba(0, 0, 0, 0.46);
      }
      .brand:focus-visible {
        outline: 2px solid #fff;
        outline-offset: 2px;
      }
      .brand .nm {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .brand .chev {
        opacity: 0.7;
        font-size: 10px;
      }
      .dot {
        width: 8px;
        height: 8px;
        flex: none;
        border-radius: 50%;
        background: var(--sb-glow);
        box-shadow: 0 0 10px var(--sb-glow);
      }
      .dot.off {
        background: rgba(255, 255, 255, 0.5);
        box-shadow: none;
      }
      .menu {
        position: absolute;
        top: calc(100% + 6px);
        left: 0;
        min-width: 210px;
        padding: 6px;
        background: rgba(20, 20, 26, 0.94);
        backdrop-filter: blur(22px);
        border: 1px solid var(--sb-line);
        border-radius: 14px;
        box-shadow: 0 20px 44px -16px rgba(0, 0, 0, 0.72);
        z-index: 5;
      }
      .menu button {
        display: flex;
        align-items: center;
        gap: 9px;
        width: 100%;
        border: none;
        background: none;
        color: var(--sb-ink);
        font: inherit;
        font-size: 13px;
        text-align: left;
        padding: 9px 10px;
        border-radius: 9px;
        cursor: pointer;
      }
      .menu button:hover {
        background: var(--sb-elev-2);
      }
      .menu button[aria-current="true"] {
        color: var(--sb-glow);
      }
      .version {
        font-family: var(--sb-mono);
        font-size: 11px;
        color: rgba(255, 255, 255, 0.72);
        padding: 4px 9px;
        border-radius: 999px;
        background: rgba(0, 0, 0, 0.3);
        backdrop-filter: blur(8px);
        flex: none;
      }
      /* Apple-vibrancy: лёгкий блюр + насыщенность, невысокая непрозрачность —
         обложка под стеклом остаётся узнаваемой. */
      .glass {
        position: relative;
        z-index: 2;
        margin-top: auto;
        width: 100%;
        box-sizing: border-box;
        padding: 16px 22px 16px;
        background: linear-gradient(
          to top,
          rgba(8, 8, 12, 0.78) 0%,
          rgba(8, 8, 12, 0.5) 60%,
          rgba(8, 8, 12, 0.22) 100%
        );
        backdrop-filter: blur(18px) saturate(1.6);
        -webkit-backdrop-filter: blur(18px) saturate(1.6);
        transition: padding 0.45s ease, opacity 0.45s ease;
      }
      /* простой: «схлопываем» — прячем контролы+громкость, оставляем
         метаданные и прогресс; высота падает до минимума + лёгкая (не
         сильная) прозрачность */
      .player.idle .glass {
        padding-bottom: 14px;
        opacity: 0.82;
      }

      .tabs {
        display: inline-flex;
        gap: 4px;
        padding: 4px;
        background: var(--sb-elev);
        border: 1px solid var(--sb-line);
        border-radius: 999px;
        margin-bottom: 16px;
        align-self: flex-start;
      }
      .tab {
        appearance: none;
        border: none;
        background: transparent;
        color: var(--sb-ink-dim);
        font: inherit;
        font-size: 13px;
        font-weight: 600;
        padding: 8px 18px;
        border-radius: 999px;
        cursor: pointer;
        transition: color 0.15s, background 0.15s;
      }
      .tab[aria-selected="true"] {
        color: var(--sb-ink);
        background: var(--sb-elev-2);
      }
      .tab:focus-visible {
        outline: 2px solid var(--sb-accent);
        outline-offset: 2px;
      }

      .error {
        padding: 12px 14px;
        margin-bottom: 16px;
        background: color-mix(in srgb, #ff4d4f 20%, var(--sb-elev));
        border: 1px solid color-mix(in srgb, #ff4d4f 45%, transparent);
        color: var(--sb-ink);
        border-radius: var(--sb-radius-sm);
        font-size: 13px;
      }

      sboom-controls {
        display: block;
        margin-top: 16px;
        overflow: hidden;
        max-height: 220px;
        opacity: 1;
        transition: max-height 0.45s ease, opacity 0.35s ease,
          margin-top 0.45s ease;
      }
      /* idle: контролы+громкость схлопываются в ноль */
      .player.idle sboom-controls {
        max-height: 0;
        opacity: 0;
        margin-top: 0;
        pointer-events: none;
      }
      sboom-browse {
        display: block;
      }

      @media (max-width: 820px) {
        .wrap {
          grid-template-columns: 1fr;
          gap: 18px;
          padding: 14px 12px 32px;
        }
        .left {
          position: static;
        }
        .player {
          min-height: 0;
        }
        .glass {
          padding: 24px 18px 18px;
        }
      }
    `;
  }

  render() {
    const connected = this._state?.connected;
    const cover = this._state?.track?.cover_url || "";
    const multi = this._devices.length > 1;
    return html`
      <div
        class="wrap"
        style=${this._glow ? `--sb-glow:${this._glow}` : nothing}
        @toast=${this._onToast}
        @command-done=${this._fetchState}
      >
        <section class="left">
          ${this._error ? html`<div class="error">${this._error}</div>` : ""}

          <div
            class="player ${this._idle ? "idle" : ""}"
            @pointermove=${this._wake}
            @pointerdown=${this._wake}
            @touchstart=${this._wake}
          >
            <div class="art ${cover ? "" : "empty"}">
              ${cover ? html`<img src=${cover} alt="" />` : ""}
            </div>
            <div class="veil"></div>

            <div class="head">
              <div class="picker">
                <button
                  class="brand ${multi ? "switch" : ""}"
                  aria-haspopup=${multi ? "menu" : nothing}
                  aria-expanded=${multi ? String(this._picker) : nothing}
                  @click=${multi ? this._togglePicker : nothing}
                >
                  <span class="dot ${connected ? "" : "off"}"></span>
                  <span class="nm">${this._deviceName}</span>
                  ${multi ? html`<span class="chev">▾</span>` : ""}
                </button>
                ${this._picker && multi
                  ? html`<div class="menu" role="menu">
                      ${this._devices.map(
                        (d) => html`<button
                          role="menuitemradio"
                          aria-current=${d.entry_id === this._entryId
                            ? "true"
                            : "false"}
                          @click=${() => this._selectDevice(d.entry_id)}
                        >
                          <span class="dot"></span>${d.name}
                        </button>`
                      )}
                    </div>`
                  : ""}
              </div>
              ${this._version
                ? html`<span class="version">v${this._version}</span>`
                : ""}
            </div>

            <div class="glass">
              <sboom-nowplaying
                .state=${this._state}
                @seek=${this._onSeek}
              ></sboom-nowplaying>
              <sboom-controls
                .hass=${this.hass}
                .state=${this._state}
                .entryId=${this._entryId}
              ></sboom-controls>
            </div>
          </div>
        </section>

        <section class="right">
          <sboom-browse
            .hass=${this.hass}
            .entryId=${this._entryId}
            .currentTrackId=${this._state?.track?.track_id}
          ></sboom-browse>
        </section>
      </div>

      <sboom-toast></sboom-toast>
    `;
  }
}

customElements.define("sboom-panel", SboomPanel);
