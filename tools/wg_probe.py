#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wg_probe.py — точный тест создания туннеля через installtunnelservice
(как это делает client_gui.py), с полным логом состояния.

Делает: uninstall старого -> install -> пауза -> снять состояние службы,
адаптеров и wg show -> очистка (uninstall).
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONF = Path.home() / ".progvpn" / "wireguard" / "pc.conf"
WG_DIR = Path("C:/Program Files/WireGuard")
WG_EXE = WG_DIR / "wireguard.exe"
WG_BIN = WG_DIR / "wg.exe"


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> None:
    if not CONF.exists():
        print(f"[!] Нет конфига: {CONF}")
        sys.exit(1)
    if not WG_EXE.exists():
        print("[!] Нет wireguard.exe")
        sys.exit(1)

    tmp = Path(tempfile.gettempdir())
    log_f = tmp / "wgprobe_log.txt"
    mark_f = tmp / "wgprobe_mark.txt"

    ps1 = tmp / "wgprobe_run.ps1"
    ps1.write_text(f"""$ErrorActionPreference = 'Continue'
$log = {_q(str(log_f))}
$mark = {_q(str(mark_f))}
Remove-Item $log,$mark -ErrorAction SilentlyContinue
function W($s) {{ Add-Content -Path $log -Value $s }}
W '== 1) uninstall old tunnel =='
& {_q(str(WG_EXE))} /uninstalltunnelservice pc 2>&1 | Out-Null
Start-Sleep -Seconds 1
W '== 2) installtunnelservice =='
& {_q(str(WG_EXE))} /installtunnelservice {_q(str(CONF))} 2>&1 | ForEach-Object {{ W $_ }}
W ('install exit: ' + $LASTEXITCODE)
Start-Sleep -Seconds 8
W '== 3) sc query =='
sc.exe query WireGuardTunnel$pc 2>&1 | ForEach-Object {{ W $_ }}
W '== 4) wireguard adapters =='
Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {{ $_.InterfaceDescription -like '*WireGuard*' }} | ForEach-Object {{ W ('ADAPTER: ' + $_.Name + ' | ' + $_.Status) }}
W '== 5) wg show pc =='
& {_q(str(WG_BIN))} show pc 2>&1 | ForEach-Object {{ W $_ }}
W '== 6) cleanup =='
& {_q(str(WG_EXE))} /uninstalltunnelservice pc 2>&1 | Out-Null
W ('cleanup exit: ' + $LASTEXITCODE)
Set-Content -Path $mark -Value 'DONE'
""", encoding="utf-8-sig")

    ps = (
        "$p = Start-Process -FilePath 'powershell.exe' "
        "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File'," +
        _q(str(ps1)) + ") -Verb RunAs -Wait -PassThru;"
        "Write-Output ('EXIT:' + $p.ExitCode)"
    )
    print("Запускаю тест от администратора (разрешите UAC)…\n")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   capture_output=True, text=True, timeout=180)
    time.sleep(1)
    if log_f.exists():
        print(log_f.read_text(encoding="utf-8", errors="replace"))
    else:
        print("Лог не создан.")
    print("== ЗАВЕРШЕНО ==")


if __name__ == "__main__":
    main()
