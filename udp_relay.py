#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
udp_relay.py — UDP-проброс для WireGuard-сервера в WSL2 (Windows 10).

Зачем: сервер WireGuard живёт внутри WSL2 на внутреннем IP (172.28.x.x),
который не виден снаружи. Этот скрипт слушает UDP-порт на самом Windows-хосте
и пересылает пакеты в WSL, а ответы — обратно. Так клиенты (телефон, другая
машина, виртуалка) могут подключаться по адресу хоста.

Использование:
    python udp_relay.py [внешний_порт] [IP_WSL] [порт_WSL]

Пример:
    python udp_relay.py 54321 172.28.202.176 51820

(внешний порт может отличаться от порта сервера в WSL, напр. если 51820
 занят/зарезервирован Windows — берём 54321, а в WSL сервер слушает 51820)

Требуется: разрешить порт в брандмауэре Windows (от администратора):
    netsh advfirewall firewall add rule name="ProGVPN UDP 51820" dir=in action=allow protocol=UDP localport=51820
"""
from __future__ import annotations

import socket
import sys
import threading
import time

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 51820
UPSTREAM_HOST = sys.argv[2] if len(sys.argv) > 2 else "172.28.202.176"
# Порт WireGuard внутри WSL (обычно 51820). Может отличаться от внешнего
# LISTEN_PORT, если внешний занят/зарезервирован Windows.
UPSTREAM_PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 51820
SESSION_TIMEOUT = 90  # сек без трафика — закрываем соединение клиента

# клиентский адрес -> {"sock": сокет в WSL, "last": время последнего пакета}
sessions: dict[tuple, dict] = {}
lock = threading.Lock()
main_sock: socket.socket | None = None


def reader(up_sock: socket.socket, client_addr: tuple) -> None:
    """Читаем ответы из WSL и шлём клиенту."""
    while True:
        try:
            data, _ = up_sock.recvfrom(65535)
        except OSError:
            break
        try:
            main_sock.sendto(data, client_addr)  # type: ignore[arg-type]
        except OSError:
            break
    with lock:
        sessions.pop(client_addr, None)
    try:
        up_sock.close()
    except OSError:
        pass


def cleanup() -> None:
    """Убираем простаивающие сессии."""
    while True:
        time.sleep(10)
        now = time.monotonic()
        with lock:
            stale = [a for a, s in sessions.items()
                     if now - s["last"] > SESSION_TIMEOUT]
            for a in stale:
                s = sessions.pop(a)
                try:
                    s["sock"].close()
                except OSError:
                    pass
        print(f"[relay] активных клиентов: {len(sessions)}")


def main() -> None:
    global main_sock
    main_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    main_sock.bind((LISTEN_HOST, LISTEN_PORT))
    main_sock.settimeout(0.5)
    print(f"✔ UDP-релей: слушаю {LISTEN_HOST}:{LISTEN_PORT}")
    print(f"  пересылаю в WSL: {UPSTREAM_HOST}:{UPSTREAM_PORT}")
    print("  В конфиге клиента указывайте endpoint = IP ЭТОГО хоста (не WSL!)")
    print("  Ctrl+C — остановить.")
    threading.Thread(target=cleanup, daemon=True).start()

    while True:
        try:
            data, client_addr = main_sock.recvfrom(65535)
        except socket.timeout:
            continue
        except OSError:
            # Windows: WSAECONNRESET (10054) приходит после ICMP «порт недоступен»
            # от ушедшего клиента. Не критично — просто продолжаем слушать.
            continue
        except KeyboardInterrupt:
            print("\nОстановлен.")
            return

        with lock:
            sess = sessions.get(client_addr)
            if sess is None:
                up = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                up.connect((UPSTREAM_HOST, UPSTREAM_PORT))
                sess = {"sock": up, "last": time.monotonic()}
                sessions[client_addr] = sess
                print(f"[relay] + клиент {client_addr[0]}:{client_addr[1]}"
                      f" → {UPSTREAM_HOST}:{UPSTREAM_PORT}")
                threading.Thread(target=reader, args=(up, client_addr),
                                 daemon=True).start()
            sess["last"] = time.monotonic()
        try:
            sess["sock"].send(data)
        except OSError:
            pass


if __name__ == "__main__":
    main()
