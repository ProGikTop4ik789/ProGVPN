#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tunnel_diag.py — диагностика запуска WireGuard-туннеля на Windows.

Запускает `wireguard.exe /tunnelservice <conf>` в консольном режиме от
администратора (появится окно UAC — разрешите) и печатает вывод/ошибку.
Использование:  python tunnel_diag.py [путь_к_конфигу.conf]
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

STATE_DIR = Path.home() / ".progvpn"
DEFAULT_CONF = STATE_DIR / "wireguard" / "test.conf"


def find_wireguard_exe() -> str | None:
    for base in (os.environ.get("PROGRAMFILES", "C:/Program Files"),
                 os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")):
        p = Path(base) / "WireGuard" / "wireguard.exe"
        if p.exists():
            return str(p)
    return None


def run_ps1_elevated(ps1_path: str) -> int:
    ps = (
        "$p = Start-Process -FilePath 'powershell.exe' "
        "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File'," +
        _q(ps1_path) + ") -Verb RunAs -Wait -PassThru;"
        "Write-Output ('EXIT:' + $p.ExitCode)"
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=300)
    out = proc.stdout or ""
    for line in out.splitlines():
        if line.startswith("EXIT:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return proc.returncode
    return proc.returncode


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> None:
    conf = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_CONF
    wg = find_wireguard_exe()
    if not wg:
        print("[!] wireguard.exe не найден.")
        sys.exit(1)
    if not conf.exists():
        print(f"[!] Конфиг не найден: {conf}")
        sys.exit(1)

    tmp = Path(tempfile.gettempdir())
    out_f = tmp / "wgdiag_out.txt"
    err_f = tmp / "wgdiag_err.txt"
    mark_f = tmp / "wgdiag_mark.txt"

    ps1 = tmp / "wgdiag_run.ps1"
    ps1.write_text(f"""$ErrorActionPreference = 'Continue'
$exe = {_q(wg)}
$conf = {_q(str(conf))}
$out = {_q(str(out_f))}
$err = {_q(str(err_f))}
$mark = {_q(str(mark_f))}
Remove-Item $out,$err,$mark -ErrorAction SilentlyContinue
$p = Start-Process -FilePath $exe -ArgumentList @('/tunnelservice', $conf) `
    -RedirectStandardOutput $out -RedirectStandardError $err -PassThru
if ($p.WaitForExit(8000)) {{
    Set-Content -Path $mark -Value ('EXIT:' + $p.ExitCode)
}} else {{
    try {{ $p.Kill() }} catch {{}}
    Set-Content -Path $mark -Value 'EXIT:TIMEOUT_RUNNING'
}}
""", encoding="utf-8")

    print(f"Конфиг : {conf}")
    print(f"WireGuard: {wg}")
    print("Запускаю туннель от администратора (разрешите UAC)…\n")
    code = run_ps1_elevated(str(ps1))
    print(f"PowerShell вернул: {code}\n")

    mark = mark_f.read_text(encoding="utf-8", errors="replace") if mark_f.exists() else "?"
    print(f"Результат: {mark}")
    for name, f in (("STDOUT", out_f), ("STDERR", err_f)):
        if f.exists():
            txt = f.read_text(encoding="utf-8", errors="replace").strip()
            if txt:
                print(f"--- {name} ---")
                print(txt)
    print()
    if mark.strip() == "EXIT:TIMEOUT_RUNNING":
        print("✔ Туннель СТАРТОВАЛ и работал 8 секунд — конфиг в порядке.")
        print("  Значит, проблема была в установке службы, а не в конфиге.")
    else:
        print("Туннель завершился с ошибкой — текст выше объясняет причину.")


if __name__ == "__main__":
    main()
