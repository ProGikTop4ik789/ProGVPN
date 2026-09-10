#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ProGVPN — server.py
===================
Python-обёртка над WireGuard (команды `wg` / `wg-quick`) для СЕРВЕРА.

Что умеет:
  * init          — сгенерировать ключи сервера и базовые настройки
  * peer-add      — создать клиента: сгенерировать ключи + preshared key,
                    выдать IP, прописать peer в конфиг сервера и
                    экспортировать готовый .conf для клиента
  * peer-list     — список клиентов
  * peer-del      — удалить клиента
  * conf          — пересобрать конфиг сервера из сохранённого состояния
  * up            — записать конфиг в /etc/wireguard и поднять интерфейс
  * down          — опустить интерфейс
  * status        — показать состояние интерфейса (`wg show`)

Требования:
  * Linux-сервер с установленным пакетом wireguard-tools (команда `wg`)
  * для `up` / `down` нужны права root (запускать через sudo)
  * включённый IP forwarding + NAT (iptables/nftables) — команды в README

Состояние хранится в ~/.progvpn/server/  (ключи и список клиентов).
Экспортированные конфиги клиентов падают в ./clients/ рядом со скриптом.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
#  Константы / пути
# ----------------------------------------------------------------------------

APP_NAME = "ProGVPN"
DEFAULT_IFACE = "wgvpn0"          # имя WireGuard-интерфейса
DEFAULT_NETWORK = "10.8.0.0/24"   # подсеть VPN-туннеля
DEFAULT_PORT = 51820              # UDP-порт сервера
DEFAULT_DNS = "1.1.1.1"           # DNS, который получит клиент


def user_home_dir() -> Path:
    """Домашняя папка пользователя.

    Если скрипт запущен через `sudo` от обычного пользователя (переменная
    SUDO_USER), `HOME` меняется на /root, и состояние искалось бы в
    /root/.progvpn. Здесь мы берём home НАСТОЯЩЕГО пользователя, чтобы
    `python3 server.py init` и `sudo python3 server.py up` видели одни и те же
    файлы.
    """
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and os.name == "posix":
        try:
            import pwd  # только на Linux/macOS
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except (KeyError, ImportError):
            pass
    return Path.home()


STATE_DIR = user_home_dir() / ".progvpn"
SERVER_DIR = STATE_DIR / "server"
KEYS_FILE = SERVER_DIR / "keys.json"     # {private_key, address, prefix, port, endpoint}
PEERS_FILE = SERVER_DIR / "peers.json"   # [{name, public_key, preshared_key, allowed_ip, endpoint}]

EXPORT_DIR = Path(__file__).resolve().parent / "clients"   # готовые .conf для клиентов

# Куда писать "активный" конфиг для wg-quick. На Linux с root — стандартный путь.
if sys.platform.startswith("linux") and os.geteuid() == 0:
    ACTIVE_DIR = Path("/etc/wireguard")
else:
    ACTIVE_DIR = STATE_DIR / "wireguard"


# ----------------------------------------------------------------------------
#  Низкоуровневые обёртки над wg
# ----------------------------------------------------------------------------

def require_wg() -> None:
    """Проверяем, что в системе есть утилита `wg` (wireguard-tools)."""
    if shutil.which("wg") is None:
        print("[!] Утилита `wg` не найдена.")
        print("    Установите wireguard-tools, например:")
        print("      Debian/Ubuntu:  sudo apt install wireguard-tools")
        print("      RHEL/Fedora:    sudo dnf install wireguard-tools")
        sys.exit(1)


def run(cmd: list[str], stdin_data: str | None = None) -> str:
    """Запустить команду и вернуть stdout (обрезок). Бросает ошибку при неудаче."""
    try:
        proc = subprocess.run(
            cmd,
            input=stdin_data,
            text=True,
            capture_output=True,
        )
    except FileNotFoundError as exc:
        sys.exit(f"[!] Команда не найдена: {exc.filename}")
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"код возврата {proc.returncode}"
        raise RuntimeError(f"Команда {' '.join(cmd)} упала: {err}")
    return proc.stdout.strip()


def gen_private_key() -> str:
    return run(["wg", "genkey"])


def public_key(private_key: str) -> str:
    """Публичный ключ = приватный, прогнанный через `wg pubkey`."""
    return run(["wg", "pubkey"], stdin_data=private_key + "\n")


def gen_preshared_key() -> str:
    return run(["wg", "genpsk"])


# ----------------------------------------------------------------------------
#  Работа с состоянием (JSON-хранилища)
# ----------------------------------------------------------------------------

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def load_server_keys() -> dict:
    return _load_json(KEYS_FILE, {})


def save_server_keys(keys: dict) -> None:
    _save_json(KEYS_FILE, keys)


def load_peers() -> list[dict]:
    return _load_json(PEERS_FILE, [])


def save_peers(peers: list[dict]) -> None:
    _save_json(PEERS_FILE, peers)


def require_init() -> dict:
    """Если сервер ещё не инициализирован — подсказываем и выходим."""
    keys = load_server_keys()
    if not keys.get("private_key"):
        sys.exit("[!] Сервер не инициализирован. Сначала выполните:  python server.py init")
    return keys


# ----------------------------------------------------------------------------
#  Генерация конфигов
# ----------------------------------------------------------------------------

def format_address(ip: str, prefix: int) -> str:
    return f"{ip}/{prefix}"


def server_conf_text(keys: dict, peers: list[dict]) -> str:
    """Полный конфиг сервера, который ляжет в /etc/wireguard/<iface>.conf."""
    lines = [
        "[Interface]",
        f"Address = {format_address(keys['address'], keys['prefix'])}",
        f"ListenPort = {keys['port']}",
        f"PrivateKey = {keys['private_key']}",
    ]
    for peer in peers:
        lines += [
            "",
            f"# Клиент: {peer['name']}",
            "[Peer]",
            f"PublicKey = {peer['public_key']}",
            f"PresharedKey = {peer['preshared_key']}",
            f"AllowedIPs = {peer['allowed_ip']}/32",
        ]
        if peer.get("endpoint"):
            lines.append(f"Endpoint = {peer['endpoint']}")
    return "\n".join(lines) + "\n"


def client_conf_text(
    client_private: str,
    client_ip: str,
    server_public: str,
    psk: str,
    endpoint: str,
    dns: str,
    prefix: int,
) -> str:
    """Готовый конфиг для КЛИЕНТА (импортируется в WireGuard-приложение)."""
    return "\n".join([
        "[Interface]",
        f"Address = {format_address(client_ip, prefix)}",
        f"PrivateKey = {client_private}",
        f"DNS = {dns}",
        "",
        "[Peer]",
        f"PublicKey = {server_public}",
        f"PresharedKey = {psk}",
        f"Endpoint = {endpoint}",
        "AllowedIPs = 0.0.0.0/0, ::/0",   # гнать ВЕСЬ трафик через VPN
        "PersistentKeepalive = 25",        # держим NAT открытым на клиенте
        "",
    ])


def write_active_conf(keys: dict, peers: list[dict], iface: str) -> Path:
    """Записать конфиг в ACTIVE_DIR/<iface>.conf и вернуть путь."""
    ACTIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = ACTIVE_DIR / f"{iface}.conf"
    path.write_text(server_conf_text(keys, peers), encoding="utf-8")
    # wg-quick не любит слишком открытые права на приватный ключ
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


# ----------------------------------------------------------------------------
#  Логика команд
# ----------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> None:
    if load_server_keys().get("private_key"):
        print("[!] Сервер уже инициализирован. Файл:", KEYS_FILE)
        print("    Если нужно пересоздать — удалите его вручную.")
        return

    require_wg()
    priv = gen_private_key()
    pub = public_key(priv)
    endpoint = f"{args.public}:{args.port}" if args.public else ""

    keys = {
        "private_key": priv,
        "public_key": pub,
        "address": args.ip,
        "prefix": int(args.prefix),
        "network": str(ipaddress.ip_network(f"{args.ip}/{args.prefix}", strict=False)),
        "port": int(args.port),
        "endpoint": endpoint,   # публичный адрес сервера для клиентских конфигов
    }
    save_server_keys(keys)
    save_peers([])

    print("✔ Сервер инициализирован.")
    print(f"  Приватный ключ : сохранён в {KEYS_FILE}")
    print(f"  Публичный ключ : {pub}")
    print(f"  Туннельный IP  : {args.ip}/{args.prefix}")
    print(f"  UDP-порт       : {args.port}")
    if endpoint:
        print(f"  Endpoint       : {endpoint}")
    print()
    print("Дальше:  python server.py up          (поднять интерфейс)")
    print("         python server.py peer-add имя_клиента --endpoint 1.2.3.4:51820")


def _find_free_client_ip(peers: list[dict], keys: dict) -> str:
    """Выдать следующий свободный IP в подсети (10.8.0.2, .3, ...)."""
    network = ipaddress.ip_network(keys["network"])
    hosts = list(network.hosts())
    taken = {p["allowed_ip"] for p in peers}
    # пропускаем адрес самого сервера
    server_ip = ipaddress.ip_address(keys["address"])
    for host in hosts:
        ip = str(host)
        if ip == str(server_ip) or ip in taken:
            continue
        return ip
    sys.exit("[!] В подсети закончились свободные IP-адреса.")


def cmd_peer_add(args: argparse.Namespace) -> None:
    keys = require_init()
    require_wg()
    peers = load_peers()

    name = args.name.strip()
    if not name:
        sys.exit("[!] Укажите имя клиента.")
    if any(p["name"] == name for p in peers):
        sys.exit(f"[!] Клиент с именем '{name}' уже существует.")

    # ВАЖНО про продакшн: в реальном мире приватный ключ клиента генерирует
    # сам клиент и присылает только публичный. Здесь мы генерируем оба сразу,
    # чтобы сразу выдать готовый .conf — удобно для демо/личного VPN.
    client_priv = gen_private_key()
    client_pub = public_key(client_priv)
    psk = gen_preshared_key()
    client_ip = _find_free_client_ip(peers, keys)

    # Endpoint: явный аргумент > сохранённый на init > заглушка
    endpoint = (
        args.endpoint
        or keys.get("endpoint")
        or "YOUR_SERVER_IP:51820"
    )

    peer = {
        "name": name,
        "public_key": client_pub,
        "preshared_key": psk,
        "allowed_ip": client_ip,
        "endpoint": endpoint,
    }
    peers.append(peer)
    save_peers(peers)

    # Экспортируем готовый конфиг клиента
    conf = client_conf_text(
        client_private=client_priv,
        client_ip=client_ip,
        server_public=keys["public_key"],
        psk=psk,
        endpoint=endpoint,
        dns=args.dns,
        prefix=keys["prefix"],
    )
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_file = EXPORT_DIR / f"{name}.conf"
    out_file.write_text(conf, encoding="utf-8")

    print(f"✔ Клиент '{name}' создан.")
    print(f"  IP в туннеле   : {client_ip}")
    print(f"  Публичный ключ : {client_pub}")
    print(f"  Конфиг клиента : {out_file}")
    print()
    print("   Скопируйте этот .conf на машину клиента и там:")
    print("     python client.py import \"путь/к/файлу.conf\"")
    print("     python client.py up")
    print()
    print("   (Или импортируйте .conf в официальное приложение WireGuard.)")
    print()
    print("Не забудьте перезапустить интерфейс сервера, чтобы peer применился:")
    print("  sudo python server.py up")


def cmd_peer_list(_args: argparse.Namespace) -> None:
    keys = require_init()
    peers = load_peers()
    print(f"Сервер: {keys['address']}/{keys['prefix']}  (порт {keys['port']})")
    print(f"Публичный ключ сервера: {keys['public_key']}")
    print()
    if not peers:
        print("Клиентов пока нет.")
        return
    print(f"{'Имя':<16}{'IP':<16}{'Endpoint':<28}Публичный ключ")
    print("-" * 110)
    for p in peers:
        print(f"{p['name']:<16}{p['allowed_ip']:<16}{p.get('endpoint', ''):<28}{p['public_key']}")


def cmd_peer_del(args: argparse.Namespace) -> None:
    require_init()
    peers = load_peers()
    before = len(peers)
    peers = [p for p in peers if p["name"] != args.name]
    if len(peers) == before:
        sys.exit(f"[!] Клиент '{args.name}' не найден.")
    save_peers(peers)
    # пробуем убрать экспортированный конфиг
    exported = EXPORT_DIR / f"{args.name}.conf"
    if exported.exists():
        exported.unlink()
    print(f"✔ Клиент '{args.name}' удалён.")
    print("  Не забудьте перезапустить интерфейс:  sudo python server.py up")


def cmd_conf(_args: argparse.Namespace) -> None:
    keys = require_init()
    print(server_conf_text(keys, load_peers()))


def cmd_up(args: argparse.Namespace) -> None:
    keys = require_init()
    require_wg()
    _check_root()

    conf_path = write_active_conf(keys, load_peers(), args.iface)
    print(f"✔ Конфиг записан: {conf_path}")

    # wg-quick принимает и имя интерфейса, и путь к файлу конфига.
    run(["wg-quick", "up", str(conf_path)])
    print("✔ Интерфейс поднят.")
    _apply_nat(keys, args.iface)


def cmd_down(args: argparse.Namespace) -> None:
    require_init()
    require_wg()
    _check_root()
    conf_path = ACTIVE_DIR / f"{args.iface}.conf"
    if not conf_path.exists():
        sys.exit(f"[!] Нет конфига {conf_path}")
    run(["wg-quick", "down", str(conf_path)])
    print("✔ Интерфейс опущен.")


def cmd_status(args: argparse.Namespace) -> None:
    require_init()
    iface = args.iface

    # 1) Проверяем существование интерфейса через `ip link` — работает БЕЗ root
    link = subprocess.run(["ip", "link", "show", iface], text=True, capture_output=True)
    if link.returncode != 0:
        print(f"Интерфейс '{iface}' сейчас не поднят.")
        print("  Поднять:  sudo python3 server.py up")
        return
    print(f"✔ Интерфейс '{iface}' существует и поднят на уровне ядра.")

    # 2) Детали WireGuard видны только root (нужен CAP_NET_ADMIN)
    wg_proc = subprocess.run(["wg", "show", iface], text=True, capture_output=True)
    if wg_proc.returncode == 0:
        print(wg_proc.stdout.strip())
    else:
        print("Детали WireGuard (ключи, handshake клиентов) видны только root:")
        print("  sudo python3 server.py status")


# ----------------------------------------------------------------------------
#  Вспомогательные функции
# ----------------------------------------------------------------------------

def _check_root() -> None:
    """wg-quick up/down требуют root на Linux."""
    if not sys.platform.startswith("linux"):
        return
    if os.geteuid() != 0:
        sys.exit("[!] Нужны права root. Запустите:  sudo python server.py ...")


def _apply_nat(keys: dict, iface: str) -> None:
    """Включить форвардинг и MASQUERADE, чтобы клиенты получали интернет.

    Вызывается автоматически при `up`. После перезагрузки WSL/системы правила
    сбрасываются, поэтому эта функция каждый раз настраивает NAT заново.
    """
    if not sys.platform.startswith("linux"):
        return
    net = keys["network"]

    # 1) ip_forward
    p = subprocess.run(["sysctl", "-w", "net.ipv4.ip_forward=1"],
                       capture_output=True, text=True)
    if p.returncode == 0:
        print("✔ ip_forward включён.")
    else:
        print(f"[i] sysctl ip_forward: {p.stderr.strip()}")

    # 2) правило MASQUERADE, если его ещё нет
    try:
        check = subprocess.run(
            ["iptables", "-t", "nat", "-C", "POSTROUTING",
             "-s", net, "!", "-o", iface, "-j", "MASQUERADE"],
            capture_output=True,
        )
    except FileNotFoundError:
        print("[!] iptables не найден. Установите его и повторите up:")
        print("      sudo apt install -y iptables")
        return
    if check.returncode == 0:
        print("✔ MASQUERADE уже настроен.")
        return
    add = subprocess.run(
        ["iptables", "-t", "nat", "-A", "POSTROUTING",
         "-s", net, "!", "-o", iface, "-j", "MASQUERADE"],
        capture_output=True, text=True,
    )
    if add.returncode == 0:
        print("✔ NAT включён: клиенты получат доступ в интернет.")
    else:
        print(f"[i] Не удалось добавить MASQUERADE: {add.stderr.strip()}")
    print(f"  (совет: сохраните ip_forward навсегда: "
          f"echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-progvpn.conf)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="server.py",
        description="ProGVPN: Python-обёртка над WireGuard для сервера.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  python server.py init --public 1.2.3.4\n"
               "  sudo python server.py up\n"
               "  python server.py peer-add phone --endpoint 1.2.3.4:51820\n",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="создать ключи и настройки сервера")
    p.add_argument("--ip", default="10.8.0.1", help="туннельный IP сервера (default: 10.8.0.1)")
    p.add_argument("--prefix", default="24", help="префикс подсети (default: 24)")
    p.add_argument("--port", default=DEFAULT_PORT, help=f"UDP-порт (default: {DEFAULT_PORT})")
    p.add_argument("--public", default="", help="публичный IP/домен сервера, попадёт в конфиги клиентов")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("peer-add", help="добавить клиента и экспортировать его .conf")
    p.add_argument("name", help="имя клиента (например: phone, laptop)")
    p.add_argument("--endpoint", default="", help="адрес сервера для клиента, напр. 1.2.3.4:51820")
    p.add_argument("--dns", default=DEFAULT_DNS, help=f"DNS для клиента (default: {DEFAULT_DNS})")
    p.set_defaults(func=cmd_peer_add)

    p = sub.add_parser("peer-list", help="показать список клиентов")
    p.set_defaults(func=cmd_peer_list)

    p = sub.add_parser("peer-del", help="удалить клиента")
    p.add_argument("name", help="имя клиента")
    p.set_defaults(func=cmd_peer_del)

    p = sub.add_parser("conf", help="показать текущий конфиг сервера")
    p.set_defaults(func=cmd_conf)

    p = sub.add_parser("up", help="записать конфиг и поднять интерфейс (root)")
    p.add_argument("--iface", default=DEFAULT_IFACE, help=f"имя интерфейса (default: {DEFAULT_IFACE})")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="опустить интерфейс (root)")
    p.add_argument("--iface", default=DEFAULT_IFACE)
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("status", help="показать статус интерфейса")
    p.add_argument("--iface", default=DEFAULT_IFACE)
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
