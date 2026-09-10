#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ProGVPN — client.py
===================
Python-обёртка над WireGuard для КЛИЕНТА (Windows / Linux / macOS).

Рабочий процесс (самый простой):
  1. Админ сервера добавляет вас:
        python server.py peer-add laptop --endpoint 1.2.3.4:51820
     и присылает файл clients/laptop.conf
  2. Вы импортируете этот конфиг:
        python client.py import "путь/к/laptop.conf" [--name laptop]
  3. Подключаетесь:
        python client.py up
  4. Отключаетесь:
        python client.py down

Альтернатива — сгенерировать СВОИ ключи и отдать серверу только публичный:
        python client.py gen
     -> печатает публичный ключ, его нужно передать админу сервера,
        а приватный остаётся только у вас (так правильно в продакшене).

Как это работает:
  * `up` на Linux: пишет конфиг и вызывает `wg-quick up` (нужен root).
  * `up` на Windows: утилиты wg-quick обычно нет — скрипт подскажет,
    как импортировать .conf в официальное приложение WireGuard.
  * `status` / `handshake`: показывает состояние через `wg show`.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
#  Константы / пути
# ----------------------------------------------------------------------------


def user_home_dir() -> Path:
    """Домашняя папка пользователя.

    Если скрипт запущен через `sudo`, переменная HOME меняется на /root.
    Учитываем SUDO_USER, чтобы `import`/`up` искали конфиги в home настоящего
    пользователя, а не в /root.
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
WG_DIR = STATE_DIR / "wireguard"          # сюда кладём активный конфиг клиента
ACTIVE_FILE = WG_DIR / "active.json"      # какой конфиг сейчас используется

DEFAULT_NAME = "client"


# ----------------------------------------------------------------------------
#  Низкоуровневые обёртки над wg
# ----------------------------------------------------------------------------

def require_wg() -> None:
    """Проверяем наличие `wg`. На Windows его обычно нет — это не страшно,
    но для генерации ключей и статуса он нужен."""
    if shutil.which("wg") is None:
        print("[!] Утилита `wg` не найдена (нужна для генерации ключей/статуса).")
        if sys.platform.startswith("win"):
            print("    Установите официальное приложение WireGuard:")
            print("      https://www.wireguard.com/install/   (оно кладёт wg.exe)")
            print("    Либо импортируйте .conf в GUI WireGuard — скрипт подскажет.")
        else:
            print("      Debian/Ubuntu:  sudo apt install wireguard-tools")
            print("      RHEL/Fedora:    sudo dnf install wireguard-tools")
        sys.exit(1)


def run(cmd: list[str], stdin_data: str | None = None) -> str:
    try:
        proc = subprocess.run(cmd, input=stdin_data, text=True, capture_output=True)
    except FileNotFoundError as exc:
        sys.exit(f"[!] Команда не найдена: {exc.filename}")
    if proc.returncode != 0:
        err = proc.stderr.strip() or proc.stdout.strip() or f"код возврата {proc.returncode}"
        raise RuntimeError(f"Команда {' '.join(cmd)} упала: {err}")
    return proc.stdout.strip()


def gen_private_key() -> str:
    return run(["wg", "genkey"])


def public_key(private_key: str) -> str:
    return run(["wg", "pubkey"], stdin_data=private_key + "\n")


# ----------------------------------------------------------------------------
#  Хранилище активного конфига
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
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def active_conf_path() -> Path | None:
    info = _load_json(ACTIVE_FILE, {})
    p = info.get("path")
    if p and Path(p).exists():
        return Path(p)
    return None


def set_active(name: str, path: Path) -> None:
    _save_json(ACTIVE_FILE, {"name": name, "path": str(path)})
    print(f"✔ Активный конфиг: {name}  ({path})")


def _require_active() -> Path:
    path = active_conf_path()
    if path is None:
        sys.exit(
            "[!] Нет активного конфига.\n"
            "    Импортируйте .conf, присланный сервером:\n"
            "      python client.py import \"путь/к/файлу.conf\""
        )
    return path


def _check_root() -> None:
    """wg-quick up/down на Linux требует root."""
    if not sys.platform.startswith("linux"):
        return
    if os.geteuid() != 0:
        sys.exit("[!] Нужны права root. Запустите:  sudo python client.py ...")


# ----------------------------------------------------------------------------
#  Команды
# ----------------------------------------------------------------------------

def cmd_import(args: argparse.Namespace) -> None:
    """Скопировать присланный сервером .conf в хранилище и сделать активным."""
    src = Path(args.path).expanduser()
    if not src.exists():
        sys.exit(f"[!] Файл не найден: {src}")

    text = src.read_text(encoding="utf-8", errors="replace")
    if "[Interface]" not in text or "[Peer]" not in text:
        sys.exit("[!] Файл не похож на WireGuard-конфиг (нет секций [Interface]/[Peer]).")

    name = (args.name or src.stem or DEFAULT_NAME).strip()
    WG_DIR.mkdir(parents=True, exist_ok=True)
    dst = WG_DIR / f"{name}.conf"
    dst.write_text(text, encoding="utf-8")
    try:
        dst.chmod(0o600)
    except OSError:
        pass

    set_active(name, dst)
    print()
    print("Дальше:  python client.py up     (подключиться)")
    print("         python client.py status (проверить handshake)")


def cmd_gen(args: argparse.Namespace) -> None:
    """
    Сгенерировать СВОИ ключи. Публичный ключ отдайте админу сервера
    (он добавит вас через peer-add), приватный остаётся у вас.
    """
    require_wg()
    priv = gen_private_key()
    pub = public_key(priv)

    WG_DIR.mkdir(parents=True, exist_ok=True)
    priv_file = WG_DIR / (args.name + ".key")
    priv_file.write_text(priv + "\n", encoding="utf-8")
    try:
        priv_file.chmod(0o600)
    except OSError:
        pass

    print("✔ Ключи сгенерированы.")
    print(f"  Приватный ключ сохранён: {priv_file}")
    print(f"  Публичный ключ (отправьте серверу): {pub}")
    print()
    print("Дальше сервер выполнит:  python server.py peer-add ...")
    print("После того как он пришлёт .conf — импортируйте его:")
    print("  python client.py import \"путь/к/файлу.conf\"")


def cmd_up(args: argparse.Namespace) -> None:
    """Поднять туннель. Linux -> wg-quick; Windows -> подсказка для GUI."""
    conf = _require_active()

    if sys.platform.startswith("win"):
        # На Windows нет wg-quick; используем встроенный wg (из приложения WireGuard),
        # либо предлагаем GUI. Пробуем через `wg-quick`, если вдруг установлен.
        if shutil.which("wg-quick"):
            _check_root()
            run(["wg-quick", "up", str(conf)])
            print("✔ Туннель поднят.")
            return
        # Пробуем WireGuard.exe (официальный GUI) — он умеет импорт через командную строку
        wg_exe = shutil.which("wireguard")
        if wg_exe is None:
            candidates = [
                Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "WireGuard" / "wireguard.exe",
                Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "WireGuard" / "wireguard.exe",
            ]
            wg_exe = next((str(c) for c in candidates if c.exists()), None)
        if wg_exe:
            print(f"✔ Открываю WireGuard GUI и импортирую туннель из:\n    {conf}")
            subprocess.Popen([wg_exe, "/installtunnelservice", str(conf)])
            print("  В окне WireGuard нажмите «Активировать» на нужном туннеле.")
            return
        print("[!] На Windows рекомендуем официальное приложение WireGuard:")
        print("    https://www.wireguard.com/install/")
        print(f"    Импортируйте в него файл:\n      {conf}")
        return

    # Linux / macOS
    _check_root()
    run(["wg-quick", "up", str(conf)])
    print("✔ Туннель поднят.")
    print("  Проверка:  python client.py status")


def cmd_down(_args: argparse.Namespace) -> None:
    conf = _require_active()

    if sys.platform.startswith("win"):
        wg_exe = shutil.which("wg") or ""
        # если туннель поднимали как сервис — остановим через wg.exe
        try:
            run(["wg", "show"], stdin_data=None)
            proc = subprocess.run(["wg", "show", "interfaces"], text=True, capture_output=True)
            ifaces = proc.stdout.split()
            for name in ifaces:
                run(["wg", "set", name, "listen-port", "0"])  # сброс настроек, фактически отвал
            print("✔ Интерфейсы WireGuard сброшены.")
        except Exception as exc:
            print(f"[i] {exc}")
        print("  Совет: остановите туннель в GUI WireGuard (кнопка «Деактивировать»).")
        return

    _check_root()
    run(["wg-quick", "down", str(conf)])
    print("✔ Туннель опущен.")


def cmd_status(_args: argparse.Namespace) -> None:
    conf = _require_active()
    name = conf.stem

    if shutil.which("wg") is None:
        sys.exit("[!] Для status нужна утилита `wg` (есть в приложении WireGuard).")

    proc = subprocess.run(["wg", "show"], text=True, capture_output=True)
    if proc.returncode != 0 or name not in proc.stdout:
        print(f"Туннель '{name}' сейчас не поднят.")
        print("Подключиться:  python client.py up")
        return

    run(["wg", "show", name])

    # короткий хелс-чек: есть ли handshake (latest handshake не пустой)
    for line in proc.stdout.splitlines():
        if "latest handshake" in line and line.split(":")[-1].strip() not in ("", "(nothing received yet)"):
            print("\n✔ Handshake установлен — VPN работает.")
            return
    print("\n⚠ Handshake ещё не было — проверьте, что сервер запущен и порт открыт.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="client.py",
        description="ProGVPN: Python-обёртка над WireGuard для клиента.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  python client.py import laptop.conf --name laptop\n"
               "  python client.py up\n"
               "  python client.py status\n"
               "  python client.py gen   (сгенерировать свои ключи)\n",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("import", help="импортировать .conf от сервера")
    p.add_argument("path", help="путь к файлу .conf (например clients/laptop.conf)")
    p.add_argument("--name", default="", help="имя туннеля (default: имя файла)")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("gen", help="сгенерировать свои ключи (публичный отдать серверу)")
    p.add_argument("--name", default=DEFAULT_NAME, help=f"имя файла ключа (default: {DEFAULT_NAME})")
    p.set_defaults(func=cmd_gen)

    p = sub.add_parser("up", help="подключиться к VPN")
    p.set_defaults(func=cmd_up)

    p = sub.add_parser("down", help="отключиться от VPN")
    p.set_defaults(func=cmd_down)

    p = sub.add_parser("status", help="показать состояние туннеля")
    p.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
