import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function component(file, tag) {
  const registry = new Map();
  class Element extends EventTarget {
    connectedCallback() {}
    disconnectedCallback() {}
    requestUpdate() {}
  }
  const code = readFileSync(new URL(`../../custom_components/sboom_ha/www/components/${file}`, import.meta.url), "utf8")
    .replace(/^import .*;\s*$/gm, "")
    .replace(/^const _historyVersion = .*;\s*$/gm, "")
    .replace(/^await import\(.*;\s*$/gm, "");
  vm.runInNewContext(code, {
    LitElement: Element, html: (strings, ...values) => ({ strings, values }),
    css: (strings) => strings.join(""), nothing: null, CustomEvent,
    customElements: { define: (name, klass) => registry.set(name, klass), get: (name) => registry.get(name) },
    console, setTimeout, clearTimeout,
  });
  return new (registry.get(tag))();
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

test("Bluetooth ignores response from a previously selected speaker", async () => {
  const view = component("sboom-bluetooth.js", "sboom-bluetooth");
  const first = deferred();
  view.entryId = "a";
  view.connected = true;
  view.hass = { callWS: ({ entry_id }) => entry_id === "a" ? first.promise : Promise.resolve({ paired: [{ name: "B" }], scanned: [] }) };
  const old = view._request("status");
  view.entryId = "b";
  view._open = true;
  view.updated(new Map([["entryId", "a"]]));
  await new Promise(setImmediate);
  first.resolve({ paired: [{ name: "A" }], scanned: [] });
  await old;
  assert.equal(view._paired[0].name, "B");
  assert.equal(view._busy, false);
});

test("Bluetooth exposes a transport failure and permits retry", async () => {
  const view = component("sboom-bluetooth.js", "sboom-bluetooth");
  view.entryId = "a";
  view.connected = true;
  view.hass = { callWS: async () => { throw new Error("scan timed out"); } };
  await view._request("scan");
  assert.match(view._error, /scan timed out/);
  assert.equal(view._busy, false);
  view.hass = { callWS: async () => ({ paired: [], scanned: [{ name: "Phone" }] }) };
  await view._request("scan");
  assert.equal(view._error, "");
  assert.equal(view._scanned[0].name, "Phone");
});

test("History clear requires confirmation and preserves items when storage fails", async () => {
  const view = component("sboom-history.js", "sboom-history");
  let calls = 0;
  view.hass = { user: { id: "alice" }, callWS: async () => { calls += 1; throw new Error("disk full"); } };
  view._items = [{ id: "42", title: "Song" }];
  await view._clear();
  assert.equal(calls, 0);
  await view._clear();
  assert.equal(calls, 1);
  assert.equal(view._items.length, 1);
  assert.match(view._error, /disk full/);
});

test("History ignores previous user's pending response", async () => {
  const view = component("sboom-history.js", "sboom-history");
  const first = deferred();
  view.hass = { user: { id: "alice" }, callWS: () => first.promise };
  const old = view._load();
  view.hass = { user: { id: "bob" }, callWS: async () => ({ items: [{ id: "bob-song" }] }) };
  await view._load();
  first.resolve({ items: [{ id: "alice-song" }] });
  await old;
  assert.equal(view._items[0].id, "bob-song");
});

test("History requests selected ordering and emits selected item for playback", async () => {
  const view = component("sboom-history.js", "sboom-history");
  let request;
  view.hass = { user: { id: "alice" }, callWS: async (msg) => { request = msg; return { items: [] }; } };
  view._order = "frequent";
  await view._load();
  assert.equal(request.order, "frequent");
  let selected;
  view.addEventListener("history-play", (event) => { selected = event.detail; });
  view._play({ id: "42", kind: "track" });
  assert.equal(selected.id, "42");
});

test("History without an authenticated user settles without a render loop", () => {
  const view = component("sboom-history.js", "sboom-history");
  const original = view._items;
  view.updated(new Map());
  view.updated(new Map());
  assert.equal(view._items, original);
  assert.equal(view._busy, false);
});

test("Queue playback records readable names for object-shaped artists", async () => {
  const view = component("sboom-browse.js", "sboom-browse");
  let request;
  view.entryId = "living";
  view.hass = { callWS: async (msg) => { request = msg; return { success: true }; } };
  await view._play("42", "track", true, { title: "Song", artists: [{ id: "1", title: "Artist One" }, { name: "Artist Two" }] });
  assert.equal(request.history_item.subtitle, "Artist One, Artist Two");
  assert.equal(view._historyRevision, 1);
});

test("Browse keeps failed playback out of refresh and permits immediate retry", async () => {
  const view = component("sboom-browse.js", "sboom-browse");
  let calls = 0;
  view.entryId = "living";
  view.hass = { callWS: async () => { calls += 1; return { success: calls > 1 }; } };
  await view._play("42", "track", true);
  assert.equal(view._historyRevision, 0);
  await view._play("42", "track", true);
  assert.equal(calls, 2);
  assert.equal(view._historyRevision, 1);
});

test("History reloads after detaching during a pending request", async () => {
  const view = component("sboom-history.js", "sboom-history");
  const pending = deferred();
  let calls = 0;
  view.hass = { user: { id: "alice" }, callWS: () => ++calls === 1 ? pending.promise : Promise.resolve({ items: [{ id: "new" }] }) };
  const old = view._load();
  view.disconnectedCallback();
  view.connectedCallback();
  await new Promise(setImmediate);
  pending.resolve({ items: [{ id: "old" }] });
  await old;
  assert.equal(calls, 2);
  assert.equal(view._items[0].id, "new");
});

test("Open Bluetooth refreshes after reattachment", async () => {
  const view = component("sboom-bluetooth.js", "sboom-bluetooth");
  view.entryId = "a";
  view.connected = true;
  view._open = true;
  let calls = 0;
  view.hass = { callWS: async () => { calls += 1; return { paired: [], scanned: [] }; } };
  view.disconnectedCallback();
  view.connectedCallback();
  await new Promise(setImmediate);
  assert.equal(calls, 1);
});
