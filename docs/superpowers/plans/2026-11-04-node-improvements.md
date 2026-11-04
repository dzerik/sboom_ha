# SBoom panel and catalogue implementation plan

> **For agentic workers:** Use TDD and independent ownership; integrate through the interfaces below.

**Goal:** Deliver Bluetooth controls, persistent playback history and resilient search in the current HA panel.
**Architecture:** Existing WSS client and HA WebSocket handlers remain the adapters. History owns Store persistence; search owns HTTP retries/cache. Lit components reuse existing theme tokens.
**Tech Stack:** Python 3.11+, Home Assistant 2024.12+, httpx, pytest/respx, Lit.
**Spec:** `docs/superpowers/specs/2026-11-04-node-improvements-design.md`.

## Global constraints

- One WSS connection per coordinator; no new runtime dependencies.
- Keep all existing service and search response contracts.
- Work on current non-main branch; preserve existing research and local settings.
- Checkpoint before implementation; bump manifest and changelog together at each commit.
- No deployment, push or operations on real speakers.

## Review focus

- User/device changes while requests are pending must not display another speaker's data.
- Failed play must never enter history; successful play must survive storage failure.
- Concurrent history load/record/clear must not overwrite newer persisted data.
- Search timeouts and retry must stay bounded and cancellation must propagate.
- BT scan must release its lock after timeout and errors must remain visible in UI.

### Task 1: Bluetooth backend

**Files:** api.py, coordinator.py; tests/test_bluetooth.py and test_api_commands.py.
**Produces:** `SberSpeakerClient.get_scanned_bt_devices() -> list[BluetoothDevice]`;
`SboomCoordinator.async_bluetooth(action: str, mac: str | None = None) -> dict`.
Actions: `status`, `scan`, `pair`, `connect`, `disconnect`, `remove`.
Result: `{paired: [{mac,name,connected}], scanned: [{mac,name,rssi}]}`.

- [ ] Write failing op21 wire-format, per-scan timeout and coordinator action tests.
- [ ] Run focused tests and observe missing method/action failures.
- [ ] Implement request with `timeout=15`, serialized operations, validated MAC/action,
  reread paired after pair/device actions, publish listeners without optimistic BT facts.
- [ ] Verify timeout recovery, two simultaneous operations and transport errors.

### Task 2: Persistent history

**Files:** new play_history.py, services.py; tests/test_play_history.py and test_services.py.
**Produces:** `async_get_play_history(hass) -> PlayHistory`;
`PlayHistory.async_list(user_id, order='recent') -> list[dict]`;
`PlayHistory.async_record(user_id, item)`, `PlayHistory.async_clear(user_id)`;
`async_record_play(hass,user_id,deeplink,metadata=None)` best-effort helper.
Item: `{kind,id,title,subtitle,cover_url,count,first_played,last_played}`.

- [ ] Write failing tests for user isolation, dedup/count/order, max100, reload,
  malformed storage, concurrent operations, failed save and success/failure recording.
- [ ] Implement HA Store version1/key `sboom_ha.play_history`, lock all state mutations,
  await storage writes, safe deeplink parsing and successful service-call recording.
- [ ] Verify one record per multi-target launch and no change to play result on storage error.

### Task 3: Catalogue resilience

**Files:** zvuk_client.py; tests/test_zvuk_resilience.py and existing catalogue tests.
**Produces:** existing `search(query,limit)` shape with dual search, bounded retries,
60s fresh/15min stale cache, max64 LRU keys and copy-on-return.

- [ ] Write failing respx tests for REST failure + GraphQL success, merge/dedup,
  429/503 retry, no retry400, cache expiry/eviction, stale only on transient errors,
  concurrent requests and cache mutation isolation.
- [ ] Implement typed normalization and bounded backoff; retain one token refresh401,
  propagate cancellation and avoid unbounded work/caches.
- [ ] Run all existing Zvuk search/drilldown tests with new resilience tests.

### Task 4: HA API and panel integration

**Files:** websocket_api.py, new www/components/sboom-bluetooth.js and
sboom-history.js, www/sboom-panel.js, www/components/sboom-browse.js;
tests/test_panel_features.py and UI smoke tests.

- [ ] Write failing WS tests: `sboom/bluetooth {action,mac_address?,entry_id?}`;
  `sboom/history {order?}`; `sboom/history_clear`; user-scoped play recording.
- [ ] Implement schemas, meaningful errors and optional history metadata on play.
- [ ] Add Bluetooth component with loading/error/empty states and history component
  with recent/frequent, repeat play and clear. Mount in panel using existing tokens.
- [ ] Reload history after successful browse launch; discard stale requests on device
  changes, disable duplicate requests, use Lit text interpolation for names/metadata.
- [ ] Verify JS syntax and exercise rendered UI against a fake HA WebSocket backend.

### Task 5: Release and review

- [ ] Run complete test suite and py_compile; address introduced lint errors.
- [ ] Update README for discoverability semantics, scan, history scope/cache behavior.
- [ ] Update manifest and CHANGELOG to 0.38.0; keep existing local settings/research.
- [ ] Independent review of the complete diff and fix substantive findings with tests.
- [ ] Commit on current branch; no merge/push/deployment.
