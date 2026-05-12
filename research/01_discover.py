"""01 — Network discovery.

Не предполагаем имя service-type заранее. Делаем два независимых подхода:

A) mDNS browse — собираем ВСЕ типы сервисов через `_services._dns-sd._udp.local.`
   meta-query. Каждый тип потом резолвим на конкретные инстансы.

B) TCP scan локальной подсети по «подозрительным» портам embedded WS-устройств
   (8000-8999, 20000-20100, 9000-9100). Устройство может быть с отключённым mDNS.

Сортировка результатов — двойная:
  - **inverse-эвристика**: service-types которых НЕТ в whitelist стандартных
    протоколов (_http, _ipp, _airplay, ...) идут в начало — proprietary
    бинарные протоколы это и есть наша цель;
  - device-class hint-слова (speaker/audio/media/...) — дополнительный bonus.

Показываем ВСЕ найденные сервисы (не только top-N): proprietary устройство
может не содержать ни одного hint-слова в имени.

Использование:
    pip install zeroconf
    python research/01_discover.py
    python research/01_discover.py --tcp-scan 192.168.1.0/24
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import socket
import time
from typing import Any


# Device-class hints — bonus points если что-то из этого встречается в name/type
HINT_KEYWORDS = (
    "speaker", "audio", "media", "cast", "tv", "display", "player", "sound",
    "music", "stream", "remote", "iot", "smart", "device", "home",
)

# Whitelist стандартных протоколов — если service-type содержит эти токены,
# это скорее всего штатный сервис ОС/принтер/Airplay/etc., НЕ наша цель.
STANDARD_PROTOCOLS = (
    "_http.", "_https.", "_ipp.", "_ipps.", "_printer.", "_pdl-datastream.",
    "_airplay.", "_raop.", "_airport.", "_companion-link.",
    "_smb.", "_afpovertcp.", "_nfs.", "_workstation.",
    "_ssh.", "_sftp-ssh.", "_telnet.", "_rdp.",
    "_ftp.", "_webdav.", "_caldav.",
    "_googlecast.", "_googlerpc.", "_googlezone.",
    "_dns-sd.",
)


def _looks_proprietary(service_type: str) -> bool:
    """True если service-type не похож на штатный протокол ОС/принтер/AirPlay/etc."""
    s = service_type.lower()
    return not any(std in s for std in STANDARD_PROTOCOLS)


def _hint_score(text: str) -> int:
    text = text.lower()
    return sum(1 for kw in HINT_KEYWORDS if kw in text)


# ─────────────────────── A) mDNS browse ───────────────────────

# Дополнительный список «известных» service-types для прямого probe'а если
# meta-query не сработал (типичный failure mode на mesh/IGMP-snooping роутерах).
# Это НЕ vendor-specific — это широко распространённые protocol-anchors,
# которые embedded-устройства часто используют как корневые точки discovery.
COMMON_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_https._tcp.local.",
    "_ipp._tcp.local.",
    "_printer._tcp.local.",
    "_airplay._tcp.local.",
    "_raop._tcp.local.",
    "_googlecast._tcp.local.",
    "_companion-link._tcp.local.",
    "_homekit._tcp.local.",
    "_hap._tcp.local.",
    "_smb._tcp.local.",
    "_ssh._tcp.local.",
    "_workstation._tcp.local.",
    "_device-info._tcp.local.",
    "_spotify-connect._tcp.local.",
    "_bose._tcp.local.",
    "_sonos._tcp.local.",
    "_axis-video._tcp.local.",
    "_dnla-playsingle._tcp.local.",
    "_mqtt._tcp.local.",
    "_amzn-wplay._tcp.local.",
]


async def _resolve_via_async_zc(timeout: float, debug: bool) -> list[dict[str, Any]]:
    """Использует AsyncZeroconfServiceTypes — официальный path для discovery
    всех service-types в подсети. Затем отдельно резолвит каждый instance.
    """
    try:
        from zeroconf.asyncio import (
            AsyncServiceBrowser,
            AsyncServiceInfo,
            AsyncZeroconf,
            AsyncZeroconfServiceTypes,
        )
    except ImportError:
        print("[!] zeroconf not installed — skip mDNS. Try: pip install zeroconf")
        return []

    discovered: list[dict[str, Any]] = []
    instance_names_per_type: dict[str, set[str]] = {}

    print(f"[+] Step A1: AsyncZeroconfServiceTypes.async_find() {timeout}s …")
    types: tuple[str, ...] = ()
    try:
        types = await AsyncZeroconfServiceTypes.async_find(timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"    [!] async_find failed: {e!r}")

    if debug:
        print(f"    [debug] discovered types ({len(types)}): {sorted(types)}")
    else:
        print(f"    found {len(types)} service-types")

    # Если meta-query пустой — пробуем прямой probe по common service-types
    if not types:
        print("[!] meta-query returned 0 types. Falling back to direct probe of "
              "common service-types (router may block multicast meta-queries).")
        types = tuple(COMMON_SERVICE_TYPES)

    types = tuple(set(types) | set(COMMON_SERVICE_TYPES))

    print(f"[+] Step A2: enumerating instances for {len(types)} types …")

    azc = AsyncZeroconf()
    try:
        from zeroconf import ServiceListener

        class _IL(ServiceListener):
            def add_service(self, zc, type_, name):
                instance_names_per_type.setdefault(type_, set()).add(name)
            def remove_service(self, zc, type_, name):
                pass
            def update_service(self, zc, type_, name):
                pass

        # Стартуем browser'ы по всем types одновременно
        browsers: list[AsyncServiceBrowser] = []
        for t in types:
            try:
                browsers.append(AsyncServiceBrowser(azc.zeroconf, t, _IL()))
            except Exception as e:  # noqa: BLE001
                if debug:
                    print(f"    [debug] browser for {t!r} failed: {e!r}")

        await asyncio.sleep(timeout)

        for b in browsers:
            try:
                await b.async_cancel()
            except Exception:  # noqa: BLE001
                pass

        # Resolve каждый instance
        total_instances = sum(len(v) for v in instance_names_per_type.values())
        if debug:
            print(f"    [debug] total instances seen: {total_instances}")
            for t, names in instance_names_per_type.items():
                print(f"           {t}: {len(names)}")

        for type_, names in instance_names_per_type.items():
            for name in names:
                info = AsyncServiceInfo(type_, name)
                try:
                    ok = await info.async_request(azc.zeroconf, 3000)
                except Exception as e:  # noqa: BLE001
                    if debug:
                        print(f"    [debug] info request {name} failed: {e!r}")
                    continue
                if not ok:
                    if debug:
                        print(f"    [debug] no info for {name}")
                    continue
                for addr in info.parsed_addresses() or []:
                    discovered.append({
                        "mdns_type": type_,
                        "name": name,
                        "host": addr,
                        "port": info.port,
                        "properties": {
                            (k.decode() if isinstance(k, bytes) else k):
                            (v.decode(errors="replace") if isinstance(v, bytes) else v)
                            for k, v in (info.properties or {}).items()
                        },
                    })
    finally:
        try:
            await azc.async_close()
        except Exception:  # noqa: BLE001
            pass

    return discovered


async def mdns_browse(timeout: float = 8.0, debug: bool = False) -> list[dict[str, Any]]:
    return await _resolve_via_async_zc(timeout, debug)


# ─────────────────────── B) TCP scan ───────────────────────

CANDIDATE_PORTS = [
    8080, 8443, 8888,
    9000, 9090,
    10000,
    20000, 20001,
    49152, 49153, 49154,  # UPnP-range
]


def _tcp_check(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


async def tcp_scan(subnet: str) -> list[tuple[str, int]]:
    network = ipaddress.ip_network(subnet, strict=False)
    if network.num_addresses > 256:
        print(f"[!] subnet {subnet} too large ({network.num_addresses} addrs) — skipping")
        return []

    loop = asyncio.get_running_loop()
    found: list[tuple[str, int]] = []

    async def _check(host: str, port: int):
        ok = await loop.run_in_executor(None, _tcp_check, host, port)
        if ok:
            found.append((host, port))

    tasks = [_check(str(addr), port) for addr in network.hosts() for port in CANDIDATE_PORTS]
    await asyncio.gather(*tasks)
    return found


# ─────────────────────── main ───────────────────────

def _local_subnets() -> list[str]:
    """Лучшая попытка определить локальные /24 подсети из network-interface'ов."""
    out: list[str] = []
    try:
        import socket as _s
        hostname = _s.gethostname()
        for info in _s.getaddrinfo(hostname, None, _s.AF_INET):
            ip = info[4][0]
            if ip.startswith(("127.", "169.254.")):
                continue
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            out.append(str(net))
    except Exception:  # noqa: BLE001
        pass
    return out


async def main(args):
    print(f"[+] mDNS browse {args.mdns_timeout}s …")
    t0 = time.time()
    services = await mdns_browse(args.mdns_timeout, debug=args.debug)
    print(f"    finished in {time.time()-t0:.1f}s, {len(services)} instances\n")

    # Auto-fallback: если mDNS пуст или не нашёл proprietary — авто-TCP-scan
    auto_scan = (
        not services
        or not any(_looks_proprietary(s["mdns_type"]) for s in services)
    )
    if auto_scan and not args.tcp_scan and not args.no_auto_scan:
        nets = _local_subnets()
        if nets:
            print(f"[!] mDNS не нашёл proprietary — auto-trigger TCP scan: {nets[0]}")
            print("    (отключить можно через --no-auto-scan)")
            args.tcp_scan = nets[0]

    # Сортировка: сначала proprietary (не стандартный протокол), потом по hint-score
    def rank(s):
        text = f"{s['name']} {s['mdns_type']} {s['properties']}"
        return (
            1 if _looks_proprietary(s['mdns_type']) else 0,  # proprietary first
            _hint_score(text),                               # затем по hint-словам
        )
    services.sort(key=rank, reverse=True)

    interesting = []
    print("Все обнаруженные сервисы (сначала proprietary, потом стандартные):\n")
    for s in services:
        text = f"{s['name']} {s['mdns_type']} {s['properties']}"
        is_proprietary = _looks_proprietary(s['mdns_type'])
        hint = _hint_score(text)

        if is_proprietary:
            tag = "[PROP]"   # proprietary protocol — likely target
        elif hint > 0:
            tag = "[hint]"   # стандартный протокол, но с device-class словом
        else:
            tag = "      "   # стандартный сервис ОС/принтера

        if is_proprietary or hint > 0:
            interesting.append(s)
            print(f"  {tag} {s['mdns_type']:30s}  {s['host']}:{s['port']}  "
                  f"name={s['name'][:40]!r}")
            for k, v in s["properties"].items():
                print(f"         prop {k}={v!r}")
        else:
            print(f"  {tag} {s['mdns_type']:30s}  {s['host']}:{s['port']}  "
                  f"name={s['name'][:40]!r}")
    print()

    if args.tcp_scan:
        print(f"[+] TCP scan {args.tcp_scan} on candidate ports …")
        found = await tcp_scan(args.tcp_scan)
        for host, port in found:
            print(f"    {host}:{port} OPEN")
    print()

    print(f"[*] Hot candidates: {len(interesting)}")
    for s in interesting:
        print(f"    → next: python 02_probe.py --host {s['host']} --port {s['port']}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--mdns-timeout", type=float, default=8.0,
                   help="Seconds to listen for mDNS replies (default 8)")
    p.add_argument("--tcp-scan", default=None,
                   help="Subnet for TCP scan, e.g. 192.168.1.0/24")
    p.add_argument("--no-auto-scan", action="store_true",
                   help="Disable auto-fallback to TCP scan if mDNS finds nothing")
    p.add_argument("--debug", action="store_true",
                   help="Verbose: print discovered service-types, browser failures")
    asyncio.run(main(p.parse_args()))
