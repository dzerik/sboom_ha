"""02 — Endpoint probe.

Не предполагаем что endpoint = WebSocket. Пробуем подряд:
- TLS-handshake (raw сокет): отдаёт ли сертификат, какой?
- Plain TCP banner: что-то шлёт само?
- HTTP GET / : вернёт ли HTTP response?
- WebSocket Upgrade: примет ли?

По выводу делаем заключение что это за endpoint.

Использование:
    python research/02_probe.py --host <host> --port <port>
"""
from __future__ import annotations

import argparse
import asyncio
import socket
import ssl
from typing import Any


# ─────────────────────── TLS info ───────────────────────

def tls_handshake(host: str, port: int, timeout: float = 5.0) -> dict[str, Any]:
    """Делаем TLS-handshake и собираем info про cert/cipher."""
    raw = socket.create_connection((host, port), timeout=timeout)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        s = ctx.wrap_socket(raw, server_hostname=host)
    except ssl.SSLError as e:
        raw.close()
        return {"tls": False, "error": str(e)}

    info: dict[str, Any] = {
        "tls": True,
        "version": s.version(),
        "cipher": s.cipher(),
        "cert_DER_size": 0,
    }
    try:
        cert_der = s.getpeercert(binary_form=True)
        info["cert_DER_size"] = len(cert_der or b"")
    except Exception:  # noqa: BLE001
        pass
    s.close()
    return info


# ─────────────────────── HTTP GET / probe ───────────────────────

async def http_get(host: str, port: int, tls: bool, timeout: float = 5.0) -> str:
    """Отправляем `GET / HTTP/1.1` и читаем первые байты."""
    try:
        if tls:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=ctx), timeout=timeout
            )
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )

        writer.write(
            f"GET / HTTP/1.1\r\nHost: {host}:{port}\r\nUser-Agent: research/0.1\r\n\r\n".encode()
        )
        await writer.drain()
        data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return data.decode("latin-1", errors="replace")
    except (asyncio.TimeoutError, OSError) as e:
        return f"[no HTTP response: {e!r}]"


# ─────────────────────── WS handshake probe ───────────────────────

async def ws_probe(host: str, port: int, timeout: float = 5.0) -> str:
    """Пробуем WebSocket upgrade. Если успешно — возвращаем server-headers."""
    try:
        import websockets
    except ImportError:
        return "[websockets lib not installed]"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    url = f"wss://{host}:{port}/"
    try:
        ws = await asyncio.wait_for(
            websockets.connect(url, ssl=ctx, open_timeout=timeout, ping_interval=None),
            timeout=timeout + 1,
        )
    except Exception as e:  # noqa: BLE001
        return f"[WS handshake failed: {e!r}]"

    # Если у websockets есть response_headers (зависит от версии)
    headers = getattr(ws, "response_headers", None) or getattr(ws, "response", None)
    server = ""
    if headers:
        try:
            server = dict(headers).get("Server", "") or dict(headers).get("server", "")
        except Exception:  # noqa: BLE001
            server = str(headers)
    await ws.close()
    return f"[WS upgrade OK]  server={server!r}"


# ─────────────────────── main ───────────────────────

async def main(args):
    host, port = args.host, args.port
    print(f"[+] Probing {host}:{port}\n")

    print("─── 1. TLS handshake ──────────────────────────")
    tls_info = await asyncio.get_running_loop().run_in_executor(
        None, tls_handshake, host, port
    )
    for k, v in tls_info.items():
        print(f"    {k}: {v!r}")
    is_tls = tls_info.get("tls", False)
    print()

    print("─── 2. HTTP GET / (over TCP+TLS or plain) ─────")
    http = await http_get(host, port, tls=is_tls)
    print("    " + (http[:600].replace("\n", "\n    ") or "(empty)"))
    print()

    print("─── 3. WebSocket Upgrade (wss:// over same port) ─")
    ws = await ws_probe(host, port)
    print(f"    {ws}")
    print()

    print("─── Summary ────────────────────────────────────")
    if is_tls and "WS upgrade OK" in ws:
        print("    Endpoint = TLS WebSocket. Move to capture (03).")
    elif is_tls:
        print("    Endpoint = TLS, but no WebSocket. May be raw TCP/TLS.")
    else:
        print("    Plain TCP. Inspect HTTP/raw banner above.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True)
    p.add_argument("--port", type=int, required=True,
                   help="Port from 01_discover output")
    asyncio.run(main(p.parse_args()))
