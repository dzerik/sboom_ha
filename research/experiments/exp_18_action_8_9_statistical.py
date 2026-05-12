"""exp_18 — statistical analysis action 8/9 на op=16.

КОНТЕКСТ: exp_06/12 показали что action 8/9 двигают background_apps,
но diff содержит большой noise — устройство само reorder'ит apps.

ИДЕЯ: устройство **постоянно** reorder'ит → есть baseline-noise.
Если N раз подряд послать action=8 и сравнить N после-snapshot'ов —
**invariant change** (что повторяется во всех runs) = реальный effect.

МЕТОД:
1. baseline-snapshot
2. N=10 раз: send action → snapshot
3. Для каждого ключа который изменился, посчитать частоту изменения
4. Сравнить с control-runs (без action) той же длины
5. Keys с frequency > control_frequency = реальный signal

EXPECTED: action=8 что-то делает с focused/voice app — invariant в этом.
"""
from __future__ import annotations

import asyncio
import json
from collections import Counter

from _helpers import find_jsons, send_recv, flatten
from _shared import field


async def state_snapshot():
    raw = await send_recv(12, timeout=4.0)
    if isinstance(raw, str): raw = raw.encode()
    s = {}
    for j in find_jsons(raw):
        try:
            o = json.loads(j)
            if isinstance(o, dict) and "volume" in o:
                s = o
                break
        except json.JSONDecodeError:
            pass
    return flatten(s)


def stable_keys(d: dict) -> dict:
    """Убираем time-related поля чтобы не зашумляли."""
    return {k: v for k, v in d.items()
            if not any(s in k.lower() for s in ("time", "timestamp", "tsms", "unixtime"))}


async def measure_changes(num_runs: int, action: int | None,
                          delay_between: float = 0.5):
    """N runs: snapshot → opt. send action → snapshot → diff.
    Returns Counter ключей-которые-изменились."""
    counter = Counter()
    base = stable_keys(await state_snapshot())
    for i in range(num_runs):
        if action is not None:
            await send_recv(16, inner=field(1, 0, action), timeout=2.0)
        await asyncio.sleep(delay_between)
        cur = stable_keys(await state_snapshot())
        keys = set(base) | set(cur)
        for k in keys:
            if base.get(k) != cur.get(k):
                counter[k] += 1
        base = cur
    return counter


async def main():
    N = 10

    # Control: без action — baseline noise
    print(f"=== Control: {N} runs WITHOUT action (baseline noise) ===")
    control = await measure_changes(N, action=None, delay_between=0.7)
    print(f"  Top noisy keys (changed in {N} runs):")
    for k, c in sorted(control.items(), key=lambda x: -x[1])[:10]:
        print(f"    {c:2d}/{N}: {k}")

    # Action 8
    print(f"\n=== Action=8: {N} runs WITH action=8 ===")
    a8 = await measure_changes(N, action=8, delay_between=0.7)
    print(f"  Top changed keys:")
    for k, c in sorted(a8.items(), key=lambda x: -x[1])[:10]:
        print(f"    {c:2d}/{N}: {k}")

    print(f"\n  Signal (action=8 changes more than control):")
    for k in sorted(a8, key=lambda x: a8[x] - control.get(x, 0), reverse=True)[:10]:
        diff = a8[k] - control.get(k, 0)
        if diff > 0:
            print(f"    {a8[k]:2d}/{N} (vs control {control.get(k, 0)}/{N}, +{diff}): {k}")

    # Action 9
    print(f"\n=== Action=9: {N} runs WITH action=9 ===")
    a9 = await measure_changes(N, action=9, delay_between=0.7)
    print(f"  Top changed keys:")
    for k, c in sorted(a9.items(), key=lambda x: -x[1])[:10]:
        print(f"    {c:2d}/{N}: {k}")

    print(f"\n  Signal (action=9 changes more than control):")
    for k in sorted(a9, key=lambda x: a9[x] - control.get(x, 0), reverse=True)[:10]:
        diff = a9[k] - control.get(k, 0)
        if diff > 0:
            print(f"    {a9[k]:2d}/{N} (vs control {control.get(k, 0)}/{N}, +{diff}): {k}")


if __name__ == "__main__":
    asyncio.run(main())
