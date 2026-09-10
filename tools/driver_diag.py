#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
driver_diag.py — диагностика загрузки драйвера WireGuard на Windows.

Пытается запустить драйвер-службу WireGuard от администратора (UAC)
и печатает результат/ошибку. Это покажет, может ли драйвер вообще
загрузиться на этой системе.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def _q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def main() -> None:
    tmp = Path(tempfile.gettempdir())
    out_f = tmp / "wgd_drv_out.txt"
    err_f = tmp / "wgd_drv_err.txt"
    mark_f = tmp / "wgd_drv_mark.txt"

    ps1 = tmp / "wgd_drv_run.ps1"
    ps1.write_text(f"""$ErrorActionPreference = 'Continue'
$out = {_q(str(out_f))}
$err = {_q(str(err_f))}
$mark = {_q(str(mark_f))}
Remove-Item $out,$err,$mark -ErrorAction SilentlyContinue
sc.exe start WireGuard 1>> $out 2>> $err
$c = $LASTEXITCODE
sc.exe query WireGuard 1>> $out 2>> $err
Set-Content -Path $mark -Value ('EXIT:' + $c)
""", encoding="utf-8")

    ps = (
        "$p = Start-Process -FilePath 'powershell.exe' "
        "-ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File'," +
        _q(str(ps1)) + ") -Verb RunAs -Wait -PassThru;"
        "Write-Output ('EXIT:' + $p.ExitCode)"
    )
    print("Запускаю драйвер WireGuard от администратора (разрешите UAC)…\n")
    proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=120)
    print(f"PowerShell вернул: {proc.returncode}\n")
    mark = mark_f.read_text(encoding="utf-8", errors="replace") if mark_f.exists() else "?"
    print(f"Метка: {mark}")
    for name, f in (("STDOUT", out_f), ("STDERR", err_f)):
        if f.exists():
            txt = f.read_text(encoding="utf-8", errors="replace").strip()
            if txt:
                print(f"--- {name} ---")
                print(txt)
    print()
    if "RUNNING" in (out_f.read_text(encoding="utf-8", errors="replace") if out_f.exists() else ""):
        print("✔ Драйвер WireGuard ЗАГРУЗИЛСЯ успешно.")
    else:
        print("Драйвер не загрузился — код/текст выше объясняют причину.")


if __name__ == "__main__":
    main()
