#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ProGVPN — client_gui.py  (v2 — тёмная тема)
============================================
Графический клиент WireGuard для Windows.

Возможности:
  * все параметры .conf редактируются в формах (IP/порт сервера, ключи, ...);
  * кнопка «Открыть .conf» — данные сами заполняются из файла;
  * Подключиться / Отключиться / Статус;
  * ОДИН активный туннель: при подключении к новому серверу предыдущий
    автоматически выключается;
  * тёмный сине-фиолетовый дизайн с логотипом ProGVPN.

Требуется установленный официальный WireGuard (https://www.wireguard.com/install/).
"""

from __future__ import annotations

import base64
import io
import math
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

# запускать дочерние консольные процессы БЕЗ появления окна
_NO_WIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# ----------------------------------------------------------------------------
#  Тема оформления (тёмный сине-фиолетовый)
# ----------------------------------------------------------------------------

BG      = "#0b0e1f"   # основной фон
CARD    = "#151b3a"   # карточки
CARD2   = "#10152e"   # карточки (фон полей)
BORDER  = "#28305f"   # рамки карточек
TXT     = "#e8ebff"   # основной текст
SUB     = "#8b93c7"   # вторичный текст
ACC1    = "#3b82f6"   # синий акцент
ACC2    = "#8b5cf6"   # фиолетовый акцент
ING     = "#0d1230"   # фон полей ввода
ING_B   = "#333e7a"   # рамка полей ввода
OK      = "#22c55e"
WARN    = "#f59e0b"

DOT_OFF  = "#3a4266"   # кружок статуса — «не подключено»
DOT_ON   = "#16a34a"   # зелёный (тёмная фаза пульса)
DOT_ON_HI = "#86efac"  # яркий зелёный (светлая фаза пульса)

FONT      = ("Segoe UI", 10)
FONT_S    = ("Segoe UI", 9)
FONT_B    = ("Segoe UI", 11, "bold")
FONT_MONO = ("Consolas", 9)

APP_TITLE = "ProGVPN — WireGuard Client"


def _assets_dir() -> Path:
    """Папка с ресурсами. В скомпилированном .exe (PyInstaller) файлы
    лежат во временной папке sys._MEIPASS, а не рядом со скриптом."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "assets"
    return Path(__file__).resolve().parent / "assets"


ASSETS_DIR = _assets_dir()
LOGO_PATH = ASSETS_DIR / "logo_without_bg.png"

STATE_DIR = Path.home() / ".progvpn"
WG_DIR = STATE_DIR / "wireguard"
DEFAULT_DNS = "1.1.1.1"
DEFAULT_ALLOWED_IPS = "0.0.0.0/0, ::/0"
DEFAULT_KEEPALIVE = "25"

# ----------------------------------------------------------------------------
#  Градиентные кнопки (PIL) — синий → тёмно-фиолетовый + неоновое свечение
# ----------------------------------------------------------------------------

GPAD = 8
BTN_H = 40
BTN_R = 18

GR_NORMAL = ("#1e40af", "#6d28d9")   # глубокий синий -> тёмно-фиолетовый
GR_HOVER = ("#2563eb", "#a78bfa")    # ярче (неон)
GR_PRESS = ("#172f8c", "#4c1d95")    # нажатие
NEON = "#c7d2fe"                        # цвет неонового ободка
RING_CONNECT = "#4ade80"    # зелёный — «Подключиться»
RING_CANCEL = "#facc15"     # жёлтый — «Отмена»
RING_DISCONNECT = "#f87171"  # красный — «Отключиться"


def _lerp_hex(c1: str, c2: str, t: float) -> tuple:
    r1, g1, b1 = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
    r2, g2, b2 = (int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return (int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t))


def _mix_hex(c1: str, c2: str, t: float) -> str:
    r, g, b = _lerp_hex(c1, c2, min(1.0, max(0.0, t)))
    return f"#{r:02x}{g:02x}{b:02x}"


def _btn_font():
    for p in ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"):
        try:
            return ImageFont.truetype(p, 11)
        except Exception:
            continue
    try:
        return ImageFont.load_default()
    except Exception:
        return None


def _text_size(text: str, font) -> tuple:
    d = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    bbox = d.textbbox((0, 0), text, font=font)
    return (bbox[2] - bbox[0], bbox[3] - bbox[1])


def _btn_geo(text: str) -> tuple:
    font = _btn_font()
    tw, _ = _text_size(text, font)
    w = tw + 44 + GPAD * 2
    h = BTN_H + GPAD * 2
    x0, y0 = GPAD, GPAD
    x1, y1 = w - GPAD - 1, h - GPAD - 1
    r = min(BTN_R, (y1 - y0) // 2)
    return w, h, x0, y0, x1, y1, r


def _button_base(text: str, top: str, bottom: str, geo: tuple):
    w, h, x0, y0, x1, y1, r = geo 
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)
    grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    px = grad.load()
    span = (x1 - x0) + (y1 - y0)
    # закрашиваем ВКЛЮЧИТЕЛЬНО до x1/y1, иначе справа/снизу остаётся
    # прозрачная полоска и правые углы выглядят «срезанными»
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            t = min(1.0, ((x - x0) + (y - y0)) / max(1, span))
            px[x, y] = _lerp_hex(top, bottom, t) + (255,)
    img.paste(grad, (0, 0), mask)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([x0 + 1, y0 + 1, x1 - 1, y1 - 1], radius=r - 1,
                        outline=_lerp_hex(top, bottom, 0.6) + (255,), width=1)
    font = _btn_font()
    tw, th = _text_size(text, font)
    tx = (w - tw) // 2
    ty = (h - th) // 2
    d.text((tx + 1, ty + 1), text, font=font, fill=(0, 0, 0, 120))  # тень
    d.text((tx, ty), text, font=font, fill=(255, 255, 255, 255))
    return img


def _button_ring(geo: tuple, color: str = NEON):
    """Отдельный слой с неоновым ободком (прозрачный фон)."""
    w, h, x0, y0, x1, y1, r = geo
    ring = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(ring).rounded_rectangle(
        [x0 - 3, y0 - 3, x1 + 3, y1 + 3], radius=r + 3,
        outline=_lerp_hex(color, color, 0.0) + (255,), width=2)
    return ring


def _blend_glow(base, ring, alpha: float):
    """Накладываем ободок с заданной прозрачностью (для плавной анимации)."""
    if alpha <= 0.001:
        return base
    a = min(1.0, max(0.0, alpha))
    r2 = ring.copy()
    r2.putalpha(r2.getchannel("A").point(lambda v: int(v * a)))
    return Image.alpha_composite(base, r2)


def _photo_from(img) -> tk.PhotoImage:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return tk.PhotoImage(data=base64.b64encode(buf.getvalue()))


class GradientButton(tk.Canvas):
    """Кнопка-градиент с ПЛАВНЫМ (ease ~0.3s) появлением неоновой обводки."""

    HOVER_MS = 300

    def __init__(self, master, text: str, command, height: int = BTN_H,
                 scale: float = 1.0, pack_side: str = "left",
                 pack_padx: int = 4):
        self._scale = max(0.5, scale)
        geo0 = _btn_geo(text)
        w = max(1, round(geo0[0] * self._scale))
        h = max(1, round(geo0[1] * self._scale))
        super().__init__(master, width=w, height=h, highlightthickness=0,
                         bd=0, bg=BG, cursor="hand2")
        self._cmd = command
        self._hovering = False
        self._alpha = 0.0
        self._job = None
        self._cur_photo = None
        self._text = text
        self._cw = w
        self._base_pil, self._ring_pil = self._make_imgs(text, NEON)
        self._cid = self.create_image(w // 2, h // 2, image=self._cur_photo)
        self._show("normal", 0.0)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.pack(side=pack_side, padx=pack_padx, pady=6)

    def _make_imgs(self, label: str, ring_color: str):
        """Собираем картинки кнопки с учётом масштаба (scale)."""
        geo = _btn_geo(label)
        s = self._scale
        base = {
            "normal": _button_base(label, *GR_NORMAL, geo),
            "hover": _button_base(label, *GR_HOVER, geo),
            "press": _button_base(label, *GR_PRESS, geo),
        }
        ring = _button_ring(geo, ring_color)
        if s != 1.0:
            base = {k: v.resize((max(1, round(v.width * s)),
                                 max(1, round(v.height * s))), Image.LANCZOS)
                    for k, v in base.items()}
            ring = ring.resize((max(1, round(ring.width * s)),
                                max(1, round(ring.height * s))), Image.LANCZOS)
        return base, ring

    # ---- смена состояния кнопки (текст + цвет ободка) ----

    def set_state(self, label: str, ring_color: str) -> None:
        """Меняем надпись и цвет неонового ободка.
        Подключиться -> зелёный, Отмена -> жёлтый, Отключиться -> красный."""
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._text = label
        self._base_pil, self._ring_pil = self._make_imgs(label, ring_color)
        geo0 = _btn_geo(label)
        img_h = max(1, round(geo0[1] * self._scale))
        self._alpha = 0.0
        try:
            self.delete(self._cid)
        except Exception:
            pass
        self._cid = self.create_image(self._cw // 2, img_h // 2, image=None)
        self._show("normal", 0.0)

    # ---- отрисовка ----

    def _show(self, base_name: str, alpha: float) -> None:
        comp = _blend_glow(self._base_pil[base_name], self._ring_pil, alpha)
        ph = _photo_from(comp)
        self._cur_photo = ph
        try:
            self.itemconfigure(self._cid, image=ph)
        except Exception:
            pass

    def _animate(self, base_name: str, target_alpha: float) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        t0 = time.monotonic()
        a0 = self._alpha
        dur = self.HOVER_MS / 1000.0

        def tick():
            p = min(1.0, (time.monotonic() - t0) / max(0.001, dur))
            e = 1 - (1 - p) ** 3          # ease-out (плавное затухание)
            self._alpha = a0 + (target_alpha - a0) * e
            self._show(base_name, self._alpha)
            if p < 1.0:
                self._job = self.after(25, tick)
            else:
                self._job = None

        tick()

    # ---- события мыши ----

    def _on_enter(self, _e) -> None:
        self._hovering = True
        self._animate("hover", 1.0)

    def _on_leave(self, _e) -> None:
        self._hovering = False
        self._animate("normal", 0.0)

    def _on_press(self, _e) -> None:
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self._alpha = 0.0
        self._show("press", 0.0)

    def _on_release(self, _e) -> None:
        if self._hovering:
            self._animate("hover", 1.0)
        else:
            self._animate("normal", 0.0)
        if self._cmd:
            self._cmd()


# ----------------------------------------------------------------------------
#  Системные утилиты
# ----------------------------------------------------------------------------

def find_wireguard_exe() -> str | None:
    if sys.platform.startswith("win"):
        for base in (os.environ.get("PROGRAMFILES", "C:/Program Files"),
                     os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")):
            p = Path(base) / "WireGuard" / "wireguard.exe"
            if p.exists():
                return str(p)
    return shutil.which("wireguard")


def find_wg_exe() -> str | None:
    wg = shutil.which("wg")
    if wg:
        return wg
    wg_exe = find_wireguard_exe()
    if wg_exe:
        sib = Path(wg_exe).parent / "wg.exe"
        if sib.exists():
            return str(sib)
    return None


def _ps_squote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def run_elevated(exe: str, args: list[str], timeout: int = 180) -> int:
    """Запустить exe с правами администратора (UAC). Возвращает код."""
    arglist = ", ".join(_ps_squote(a) for a in args)
    ps = (
        "$p = Start-Process -FilePath " + _ps_squote(exe) +
        " -ArgumentList @(" + arglist + ") -WindowStyle Hidden " +
        "-Verb RunAs -Wait -PassThru;" +
        "Write-Output ('EXIT:' + $p.ExitCode)"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
            capture_output=True, text=True, timeout=timeout,
            creationflags=_NO_WIN,
        )
    except Exception:
        return -1
    for line in (proc.stdout or "").splitlines():
        if line.startswith("EXIT:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return proc.returncode
    return proc.returncode


def adapter_state(name: str) -> str:
    """Состояние адаптера: UP / DOWN / NOT_FOUND (без админа)."""
    if not sys.platform.startswith("win"):
        return "NOT_FOUND"
    safe = name.replace("'", "''")
    ps = f"(Get-NetAdapter -Name '{safe}' -ErrorAction SilentlyContinue).Status"
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=20,
                              creationflags=_NO_WIN)
        st = (proc.stdout or "").strip()
        return st.upper() if st else "NOT_FOUND"
    except Exception:
        return "?"


def active_wg_tunnels() -> list[str]:
    """Имена всех активных WireGuard-адаптеров (без админа)."""
    if not sys.platform.startswith("win"):
        return []
    ps = "(Get-NetAdapter | Where-Object { $_.InterfaceDescription -like '*WireGuard*' }).Name"
    try:
        proc = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                              capture_output=True, text=True, timeout=20,
                              creationflags=_NO_WIN)
        return [n.strip() for n in (proc.stdout or "").splitlines() if n.strip()]
    except Exception:
        return []


# --- работа с .conf ---------------------------------------------------------

def parse_conf(text: str) -> dict[str, list[tuple[str, str]]]:
    sections: dict[str, list[tuple[str, str]]] = {}
    cur: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            cur = line[1:-1].strip()
            sections.setdefault(cur, [])
            continue
        if "=" in line and cur:
            k, v = line.split("=", 1)
            sections[cur].append((k.strip(), v.strip()))
    return sections


def get_value(sections: dict, section: str, key: str) -> str | None:
    for k, v in sections.get(section, []):
        if k.lower() == key.lower():
            return v
    return None


def split_endpoint(endpoint: str) -> tuple[str, str]:
    endpoint = (endpoint or "").strip()
    if endpoint.startswith("["):
        host, _, port = endpoint[1:].partition("]:")
        return host, port
    if ":" in endpoint:
        host, _, port = endpoint.rpartition(":")
        return host, port
    return endpoint, ""


# ----------------------------------------------------------------------------
#  Приложение
# ----------------------------------------------------------------------------

class ProGVPNClientApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title(APP_TITLE)
        root.configure(bg=BG)
        try:
            root.attributes("-alpha", 1.0)  # необязательно
        except Exception:
            pass

        self.log_queue: queue.Queue[str] = queue.Queue()
        self._logo_img: tk.PhotoImage | None = None
        self._secret_entries: list[tk.Entry] = []
        self._extra_iface: list[tuple[str, str]] = []
        self._extra_peer: list[tuple[str, str]] = []

        # переменные полей
        self.v_name = tk.StringVar(value="progvpn")
        self.v_server_host = tk.StringVar(value="127.0.0.1")
        self.v_server_port = tk.StringVar(value="51820")
        self.v_address = tk.StringVar(value="10.8.0.2/24")
        self.v_priv = tk.StringVar()
        self.v_dns = tk.StringVar(value=DEFAULT_DNS)
        self.v_pub_display = tk.StringVar(value="")
        self.v_server_pub = tk.StringVar()
        self.v_psk = tk.StringVar()
        self.v_allowed = tk.StringVar(value=DEFAULT_ALLOWED_IPS)
        self.v_keepalive = tk.StringVar(value=DEFAULT_KEEPALIVE)
        self.v_show_keys = tk.BooleanVar(value=False)
        self.v_status = tk.StringVar(value="Не подключено")

        self._action_buttons: list = []
        self._busy = False
        self._phase = "idle"      # idle / working / connected
        self._cancel = False
        self._connected = False
        self._pulse_job = None

        self._make_titlebar()
        self._build_ui()
        self.root.after(60, lambda: self._center(900, 1000))
        self.root.after(200, self._drain_log)

    # ================= оформление =================

    @staticmethod
    def _mix(c1: str, c2: str, t: float) -> str:
        r1, g1, b1 = (int(c1[i:i + 2], 16) for i in (1, 3, 5))
        r2, g2, b2 = (int(c2[i:i + 2], 16) for i in (1, 3, 5))
        r = int(r1 + (r2 - r1) * t)
        g = int(g1 + (g2 - g1) * t)
        b = int(b1 + (b2 - b1) * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    def _gradient_line(self, parent, height: int, c1: str, c2: str) -> None:
        cv = tk.Canvas(parent, height=height, highlightthickness=0, bd=0, bg=BG)
        cv.pack(fill="x")
        for i in range(height):
            cv.create_line(0, i, 5000, i, fill=self._mix(c1, c2, i / max(height - 1, 1)))
        return

    def _load_logo(self, parent) -> None:
        """Показываем логотип (flatten прозрачность на фон BG)."""
        if not LOGO_PATH.exists():
            return
        try:
            from PIL import Image
            img = Image.open(LOGO_PATH).convert("RGBA")
            target_h = 130
            w = max(1, int(img.width * target_h / img.height))
            img = img.resize((w, target_h), Image.LANCZOS)
            flat = Image.new("RGB", (w, target_h), BG)
            flat.paste(img, (0, 0), img)  # прозрачность -> BG
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            flat.save(tmp.name, "PNG")
            self._logo_img = tk.PhotoImage(file=tmp.name)
            tk.Label(parent, image=self._logo_img, bg=BG).pack(pady=(16, 0))
        except Exception as exc:
            print("logo:", exc)

    def _card(self, parent, title: str) -> tk.Frame:
        """Карточка с заголовком."""
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=14, pady=6)
        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x", pady=(6, 2))
        bar = tk.Frame(head, bg=ACC1, width=4, height=20)
        bar.pack(side="left", padx=(2, 6))
        tk.Label(head, text=title, bg=BG, fg=TXT, font=FONT_B, anchor="w").pack(side="left")
        body = tk.Frame(wrap, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        body.pack(fill="x")
        return body

    def _field(self, parent, row: int, label: str, var: tk.StringVar,
               secret: bool = False, hint: str = "") -> tk.Entry:
        tk.Label(parent, text=label, bg=CARD, fg=SUB, font=FONT_S,
                 anchor="w").grid(row=row, column=0, sticky="w", padx=(12, 8), pady=(8, 0))
        e = tk.Entry(parent, textvariable=var, font=FONT, bg=ING, fg=TXT,
                     insertbackground=TXT, relief="flat",
                     highlightthickness=1, highlightbackground=ING_B,
                     highlightcolor=ACC1, width=56,
                     show="*" if secret else "")
        e.grid(row=row, column=1, sticky="we", padx=(8, 12), pady=(8, 0))
        parent.columnconfigure(1, weight=1)
        if secret:
            self._secret_entries.append(e)
            e.bind("<KeyRelease>", lambda _ev: self._refresh_public_key())
        if hint:
            tk.Label(parent, text=hint, bg=CARD, fg="#5a6391", font=("Segoe UI", 8),
                     anchor="w").grid(row=row, column=1, sticky="w", padx=(8, 12))
        return e

    def _btn(self, parent, text: str, cmd, c1: str, c2: str) -> tk.Button:
        b = tk.Button(parent, text=text, command=cmd, font=FONT_B, fg="#ffffff",
                      bg=c1, activebackground=c2, activeforeground="#ffffff",
                      relief="flat", bd=0, padx=16, pady=6, cursor="hand2")
        b.pack(side="left", padx=5, pady=4)
        b.configure(highlightthickness=0)
        return b

    # ================= кастомный заголовок и иконка =================

    def _set_window_icon(self) -> None:
        try:
            img = Image.open(LOGO_PATH).convert("RGBA").resize((64, 64), Image.LANCZOS)
            ico = _photo_from(img)
            self._icon_img = ico
            self.root.iconphoto(True, ico)
        except Exception as exc:
            print("icon:", exc)

    def _force_taskbar(self) -> None:
        """Окно overrideredirect не видно в Taskbar/Alt+Tab — чиним через WinAPI:
        добавляем стиль WS_EX_APPWINDOW и убираем WS_EX_TOOLWINDOW."""
        if not sys.platform.startswith("win"):
            return
        try:
            import ctypes
            u = ctypes.windll.user32
            hwnd = u.GetParent(self.root.winfo_id())
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            style = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style = (style & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            SWP_NOSIZE = 0x0001
            SWP_NOMOVE = 0x0002
            SWP_NOZORDER = 0x0004
            SWP_FRAMECHANGED = 0x0020
            u.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                           SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
            self._set_taskbar_icon(hwnd)
        except Exception as exc:
            print("taskbar:", exc)

    def _set_taskbar_icon(self, hwnd: int | None = None) -> None:
        """Ставим иконку на кнопку в панели задач (WM_SETICON + .ico)."""
        if not sys.platform.startswith("win"):
            return
        try:
            img = Image.open(LOGO_PATH).convert("RGBA")
            ico_path = Path(tempfile.gettempdir()) / "progvpn_app.ico"
            img.save(ico_path, format="ICO",
                     sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
            try:
                self.root.iconbitmap(default=str(ico_path))
            except Exception:
                pass
            import ctypes
            u = ctypes.windll.user32
            if hwnd is None:
                hwnd = u.GetParent(self.root.winfo_id())
            u.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                     ctypes.c_uint, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_uint]
            u.LoadImageW.restype = ctypes.c_void_p
            u.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                       ctypes.c_uint, ctypes.c_void_p]
            u.SetClassLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                           ctypes.c_void_p]
            u.SetClassLongPtrW.restype = ctypes.c_void_p
            LR_LOADFROMFILE = 0x00000010
            hicon = u.LoadImageW(None, str(ico_path), 1, 32, 32, LR_LOADFROMFILE)
            hicon_sm = u.LoadImageW(None, str(ico_path), 1, 16, 16, LR_LOADFROMFILE)
            if hicon:
                u.SendMessageW(hwnd, 0x0080, 1, hicon)   # WM_SETICON ICON_BIG
                u.SendMessageW(hwnd, 0x0080, 0, hicon_sm or hicon)  # ICON_SMALL
                u.SetClassLongPtrW(hwnd, -14, hicon)      # GCLP_HICON
                u.SetClassLongPtrW(hwnd, -34, hicon_sm or hicon)  # GCLP_HICONSM
        except Exception as exc:
            print("taskbar icon:", exc)

    def _make_titlebar(self) -> None:
        self._set_window_icon()
        self.root.overrideredirect(True)

        bar = tk.Frame(self.root, bg=CARD)
        bar.pack(fill="x")

        # маленькая иконка + название
        try:
            img = Image.open(LOGO_PATH).convert("RGBA")
            img.thumbnail((26, 26), Image.LANCZOS)
            flat = Image.new("RGB", img.size, CARD)
            flat.paste(img, (0, 0), img)
            self._bar_icon = _photo_from(flat)
            ic = tk.Label(bar, image=self._bar_icon, bg=CARD)
            ic.pack(side="left", padx=(10, 4), pady=6)
            ic.bind("<Button-1>", self._start_move)
            ic.bind("<B1-Motion>", self._on_move)
        except Exception:
            pass

        ttl = tk.Label(bar, text="  ProGVPN — WireGuard Client",
                       font=("Segoe UI", 10, "bold"), fg=TXT, bg=CARD)
        ttl.pack(side="left", pady=6)
        ttl.bind("<Button-1>", self._start_move)
        ttl.bind("<B1-Motion>", self._on_move)

        # кнопки окна (сначала close — чтобы оказаться справа)
        self._close_btn = self._title_btn(bar, "✕", self._close_win, danger=True)
        self._min_btn = self._title_btn(bar, "🗕", self._minimize, danger=False)

        # перетаскивание окна за панель
        bar.bind("<Button-1>", self._start_move)
        bar.bind("<B1-Motion>", self._on_move)

        # восстановление рамки после сворачивания
        self.root.bind("<Map>", self._on_map)

        # градиентная линия под заголовком
        self._gradient_line(self.root, 3, ACC1, ACC2)

        # появиться в Taskbar/Alt+Tab/Win+Tab (+ повторы для иконки)
        self.root.after(150, self._force_taskbar)
        self.root.after(700, self._force_taskbar)
        self.root.after(1800, self._force_taskbar)

    def _title_btn(self, parent, text: str, cmd, danger: bool):
        b = tk.Label(parent, text=text, font=("Segoe UI", 12, "bold"),
                     fg="#f87171" if danger else SUB, bg=CARD,
                     padx=12, pady=2, cursor="hand2")
        b.pack(side="right")
        b.bind("<Enter>", lambda _e: b.configure(bg="#7f1d1d" if danger else "#2b3566"))
        b.bind("<Leave>", lambda _e: b.configure(bg=CARD))
        b.bind("<Button-1>", lambda _e: cmd())
        return b

    def _start_move(self, event) -> None:
        self._offx, self._offy = event.x, event.y

    def _on_move(self, event) -> None:
        x = self.root.winfo_pointerx() - self._offx
        y = self.root.winfo_pointery() - self._offy
        self.root.geometry(f"+{x}+{y}")

    def _center(self, w: int, h: int) -> None:
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 8)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _minimize(self) -> None:
        self.root.overrideredirect(False)
        self.root.iconify()

    def _close_win(self) -> None:
        self.root.destroy()

    def _on_map(self, _event) -> None:
        if self.root.state() == "normal":
            self.root.after(80, lambda: self.root.overrideredirect(True))
            self.root.after(150, self._force_taskbar)

    def _build_ui(self) -> None:
        # --- логотип ---
        self._load_logo(self.root)

        # --- строка статуса (крупный текст + пульсирующий кружок) ---
        st_row = tk.Frame(self.root, bg=BG)
        st_row.pack(pady=(8, 2))
        self._dot = tk.Canvas(st_row, width=22, height=22, bg=BG,
                              highlightthickness=0, bd=0)
        self._dot.pack(side="left", padx=(2, 8))
        self._dot_oval = self._dot.create_oval(3, 3, 19, 19,
                                               fill=DOT_OFF, outline="")
        self._status_lbl = tk.Label(st_row, textvariable=self.v_status, bg=BG,
                                    fg=SUB, font=("Segoe UI", 17, "bold"))
        self._status_lbl.pack(side="left")

        # --- градиентная линия-разделитель ---
        self._gradient_line(self.root, 4, ACC1, ACC2)

        # ============ Туннель и сервер ============
        body = self._card(self.root, "Туннель и сервер")
        self._field(body, 0, "Имя туннеля:", self.v_name, hint="")
        self._field(body, 1, "IP / хост сервера:", self.v_server_host)
        self._field(body, 2, "Порт сервера:", self.v_server_port)

        # ============ Интерфейс (клиент) ============
        body = self._card(self.root, "Интерфейс (клиент) — из секции [Interface]")
        self._field(body, 0, "Address (мой IP):", self.v_address)
        self._field(body, 1, "PrivateKey (мой):", self.v_priv, secret=True)
        self._field(body, 2, "DNS:", self.v_dns)
        tk.Label(body, textvariable=self.v_pub_display, bg=CARD, fg=ACC1,
                 font=("Segoe UI", 8), anchor="w", wraplength=620, justify="left"
                 ).grid(row=3, column=0, columnspan=2, sticky="w", padx=12, pady=(4, 8))

        # ============ Сервер (Peer) ============
        body = self._card(self.root, "Сервер (Peer) — из секции [Peer]")
        self._field(body, 0, "PublicKey сервера:", self.v_server_pub)
        self._field(body, 1, "PresharedKey:", self.v_psk, secret=True)
        self._field(body, 2, "AllowedIPs:", self.v_allowed)
        self._field(body, 3, "PersistentKeepalive:", self.v_keepalive)

        # ============ Кнопки ============
        btns = tk.Frame(self.root, bg=BG)
        btns.pack(fill="x", padx=10, pady=(6, 0))
        # главная кнопка — крупная (x1.35) и справа, чтобы выделяться
        self.btn_action = GradientButton(btns, "Подключиться", self.on_action,
                                         scale=1.35, pack_side="right",
                                         pack_padx=10)
        self.btn_action.set_state("Подключиться", RING_CONNECT)
        self.btn_open = GradientButton(btns, "Открыть .conf", self.on_open_conf)
        self.btn_save = GradientButton(btns, "Сохранить .conf", self.on_save_conf)
        #self.btn_stat = GradientButton(btns, "Статус",
        #                               lambda: self._run_bg(self.cmd_status))
        self._action_buttons = [self.btn_open, self.btn_save, self.btn_action]

        opt = tk.Frame(self.root, bg=BG)
        opt.pack(fill="x", padx=20, pady=(0, 2))
        tk.Checkbutton(opt, text="Показывать ключи", variable=self.v_show_keys,
                       command=self._apply_key_visibility, bg=BG, fg=SUB,
                       selectcolor=BG, activebackground=BG, activeforeground=TXT,
                       font=FONT_S, highlightthickness=0).pack(side="left")

        # ============ Лог ============
        logf = tk.Frame(self.root, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1)
        logf.pack(fill="both", expand=True, padx=14, pady=(4, 12))
        tk.Label(logf, text="Лог", bg=CARD, fg=SUB, font=FONT_B,
                 anchor="w").pack(anchor="w", padx=10, pady=(6, 0))

        log_body = tk.Frame(logf, bg=CARD)
        log_body.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        self.log = tk.Text(
            log_body, height=8, bg=CARD2, fg="#c8cdf0", insertbackground=TXT,
            font=FONT_MONO, relief="flat", highlightthickness=1,
            highlightbackground=ING_B, highlightcolor=ING_B,
            state="disabled", wrap="word", padx=6, pady=4)
        self.log_sb = ttk.Scrollbar(log_body, orient="vertical",
                                    command=self.log.yview)
        self.log.configure(yscrollcommand=self.log_sb.set)
        self.log_sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._style_scrollbar()

        self._apply_key_visibility()

    # ================= вид/состояние =================

    def _style_scrollbar(self) -> None:
        """Тёмный ползунок в логе вместо стандартного светлого."""
        try:
            style = ttk.Style(self.root)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            style.configure("Vertical.TScrollbar",
                            background=CARD, troughcolor=CARD2,
                            bordercolor=BORDER, lightcolor=CARD2,
                            darkcolor=CARD2, arrowcolor=ACC1,
                            relief="flat", borderwidth=0)
            style.map("Vertical.TScrollbar",
                      background=[("active", ACC1), ("pressed", ACC2)],
                      arrowcolor=[("active", "#ffffff")],
                      troughcolor=[("pressed", CARD2)])
        except Exception as exc:
            print("scrollbar:", exc)

    def _apply_key_visibility(self) -> None:
        show = "" if self.v_show_keys.get() else "*"
        for e in self._secret_entries:
            e.configure(show=show)

    def _start_pulse(self) -> None:
        """Медленная «пульсация» кружка зелёным (~2.6s на цикл)."""
        if self._pulse_job:
            return
        t0 = time.monotonic()

        def tick():
            if not self._connected:
                self._pulse_job = None
                return
            p = time.monotonic() - t0
            val = 0.5 + 0.5 * math.sin(p * (2 * math.pi / 2.6))
            col = _mix_hex(DOT_ON, DOT_ON_HI, val)
            try:
                self._dot.itemconfigure(self._dot_oval, fill=col)
            except Exception:
                pass
            self._pulse_job = self._dot.after(60, tick)

        tick()

    def _stop_pulse(self) -> None:
        if self._pulse_job:
            try:
                self._dot.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None
        try:
            self._dot.itemconfigure(self._dot_oval, fill=DOT_OFF)
        except Exception:
            pass

    def _set_connected(self, connected: bool) -> None:
        self._connected = connected
        if connected:
            self.v_status.set("Подключено")
            self._status_lbl.configure(fg=RING_CONNECT)   # зелёное «свечение»
            self._start_pulse()
        else:
            self.v_status.set("Не подключено")
            self._status_lbl.configure(fg=SUB)
            self._stop_pulse()

    # ================= лог =================

    def _log(self, msg: str) -> None:
        self.log_queue.put(str(msg))

    def _drain_log(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log.configure(state="normal")
                self.log.insert("end", msg + "\n")
                self.log.see("end")
                self.log.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(200, self._drain_log)

    def _run_bg(self, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self._set_buttons_enabled(False)
        threading.Thread(target=lambda: self._bg_done(fn), daemon=True).start()

    def _bg_done(self, fn) -> None:
        try:
            fn()
        except Exception as exc:
            self._log(f"[ОШИБКА] {exc}")
        finally:
            self.root.after(0, self._end_busy)

    def _end_busy(self) -> None:
        self._busy = False
        self._set_buttons_enabled(True)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        """Главную кнопку (Подключиться/Отмена/Отключиться) НЕ отключаем —
        ей нужно оставаться кликабельной для отмены/отключения."""
        for b in self._action_buttons:
            if b is self.btn_action:
                continue
            try:
                b.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass

    # ================= конфиг =================

    def _safe_name(self) -> str:
        name = (self.v_name.get().strip() or "progvpn").replace(" ", "_")
        return "".join(c for c in name if c.isalnum() or c in "-_") or "progvpn"

    def _conf_path(self) -> Path:
        WG_DIR.mkdir(parents=True, exist_ok=True)
        return WG_DIR / f"{self._safe_name()}.conf"

    def build_conf_text(self) -> str:
        host = self.v_server_host.get().strip()
        port = self.v_server_port.get().strip() or "51820"
        lines = ["[Interface]"]
        if self.v_address.get().strip():
            lines.append(f"Address = {self.v_address.get().strip()}")
        if self.v_priv.get().strip():
            lines.append(f"PrivateKey = {self.v_priv.get().strip()}")
        if self.v_dns.get().strip():
            lines.append(f"DNS = {self.v_dns.get().strip()}")
        for k, v in self._extra_iface:
            if k.lower() not in ("address", "privatekey", "dns"):
                lines.append(f"{k} = {v}")
        lines += ["", "[Peer]"]
        if self.v_server_pub.get().strip():
            lines.append(f"PublicKey = {self.v_server_pub.get().strip()}")
        if self.v_psk.get().strip():
            lines.append(f"PresharedKey = {self.v_psk.get().strip()}")
        lines.append(f"Endpoint = {host}:{port}")
        if self.v_allowed.get().strip():
            lines.append(f"AllowedIPs = {self.v_allowed.get().strip()}")
        if self.v_keepalive.get().strip():
            lines.append(f"PersistentKeepalive = {self.v_keepalive.get().strip()}")
        for k, v in self._extra_peer:
            if k.lower() not in ("publickey", "presharedkey", "endpoint",
                                 "allowedips", "persistentkeepalive"):
                lines.append(f"{k} = {v}")
        return "\n".join(lines) + "\n"

    def load_conf_text(self, text: str) -> None:
        secs = parse_conf(text)
        addr = get_value(secs, "Interface", "Address") or ""
        self.v_address.set(addr.split(",")[0].strip())
        self.v_priv.set(get_value(secs, "Interface", "PrivateKey") or "")
        self.v_dns.set(get_value(secs, "Interface", "DNS") or "")
        self.v_server_pub.set(get_value(secs, "Peer", "PublicKey") or "")
        self.v_psk.set(get_value(secs, "Peer", "PresharedKey") or "")
        host, port = split_endpoint(get_value(secs, "Peer", "Endpoint") or "")
        if host:
            self.v_server_host.set(host)
        if port:
            self.v_server_port.set(port)
        self.v_allowed.set(get_value(secs, "Peer", "AllowedIPs") or DEFAULT_ALLOWED_IPS)
        self.v_keepalive.set(get_value(secs, "Peer", "PersistentKeepalive") or "")
        known_iface = {"address", "privatekey", "dns"}
        known_peer = {"publickey", "presharedkey", "endpoint", "allowedips",
                      "persistentkeepalive"}
        self._extra_iface = [(k, v) for k, v in secs.get("Interface", [])
                             if k.lower() not in known_iface]
        self._extra_peer = [(k, v) for k, v in secs.get("Peer", [])
                            if k.lower() not in known_peer]
        self._refresh_public_key()

    def _refresh_public_key(self) -> None:
        priv = self.v_priv.get().strip()
        if not priv:
            self.v_pub_display.set("Мой публичный ключ: (нет PrivateKey)")
            return
        wg = find_wg_exe()
        if not wg:
            self.v_pub_display.set("Мой публичный ключ: (нужен WireGuard)")
            return
        try:
            proc = subprocess.run([wg, "pubkey"], input=priv + "\n",
                                  text=True, capture_output=True, timeout=20,
                                  creationflags=_NO_WIN)
            if proc.returncode == 0:
                self.v_pub_display.set("Мой публичный ключ: " + proc.stdout.strip())
            else:
                self.v_pub_display.set("Не удалось вычислить ключ: " + proc.stderr.strip())
        except Exception as exc:
            self.v_pub_display.set(f"Ошибка: {exc}")

    # ================= действия =================

    def on_open_conf(self) -> None:
        path = filedialog.askopenfilename(
            title="Выберите .conf WireGuard",
            filetypes=[("WireGuard config", "*.conf"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{exc}")
            return
        if "[Interface]" not in text:
            messagebox.showerror("Ошибка", "В файле нет секции [Interface].")
            return
        self.load_conf_text(text)
        stem = Path(path).stem
        if stem:
            self.v_name.set(stem)
        self._log(f"✔ Открыт конфиг: {path}")
        self._log("   Данные заполнены. Проверьте IP/порт и нажмите «Подключиться».")

    def on_save_conf(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Сохранить конфиг", defaultextension=".conf",
            initialfile=f"{self._safe_name()}.conf",
            filetypes=[("WireGuard config", "*.conf")])
        if not path:
            return
        try:
            Path(path).write_text(self.build_conf_text(), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{exc}")
            return
        self._log(f"✔ Конфиг сохранён: {path}")

    def _require_keys(self) -> bool:
        if not self.v_server_pub.get().strip():
            messagebox.showerror("Не хватает данных",
                                 "Введите PublicKey сервера (или откройте .conf).")
            return False
        if not self.v_priv.get().strip():
            messagebox.showerror("Не хватает данных",
                                 "Введите свой PrivateKey (он есть в .conf).")
            return False
        return True

    # ---- умная кнопка: Подключиться / Отмена / Отключиться ----

    def on_action(self) -> None:
        p = self._phase
        if p == "idle":
            if not self._require_keys():
                return
            self._begin_connect()
        elif p == "connected":
            self._begin_disconnect()
        else:  # "working" — жмём «Отмена»
            self._cancel = True
            self._log("⏹ Отменяю… (дождитесь завершения текущего шага)")

    def _set_phase(self, phase: str) -> None:
        self._phase = phase
        if phase == "idle":
            self.btn_action.set_state("Подключиться", RING_CONNECT)
            self._set_connected(False)
        elif phase == "connected":
            self.btn_action.set_state("Отключиться", RING_DISCONNECT)
            self._set_connected(True)
        else:  # working
            self.btn_action.set_state("Отмена", RING_CANCEL)
            self._set_buttons_enabled(False)

    # ---- подключение (с отменой и защитой «один туннель») ----

    def _begin_connect(self) -> None:
        if self._busy:
            return
        self._cancel = False
        self._busy = True
        self._set_phase("working")
        threading.Thread(target=self._do_connect, daemon=True).start()

    def _do_connect(self) -> None:
        try:
            wg_exe = find_wireguard_exe()
            if sys.platform.startswith("win") and not wg_exe:
                self._log("[!] WireGuard не найден. Скачайте: https://www.wireguard.com/install/")
                self.root.after(0, self._end_connect, False)
                return
            conf = self._conf_path()
            conf.write_text(self.build_conf_text(), encoding="utf-8")
            self._log(f"✔ Конфиг сохранён: {conf}")
            name = self._safe_name()

            if sys.platform.startswith("win"):
                self._log("Подключаюсь… (окно UAC — разрешите)")

                # защита: только ОДИН активный туннель
                others = [n for n in active_wg_tunnels() if n != name]
                for o in others:
                    if self._cancel:
                        break
                    self._log(f"⏻ Выключаю туннель '{o}'…")
                    run_elevated(wg_exe, ["/uninstalltunnelservice", o])
                    time.sleep(1)
                if adapter_state(name) != "NOT_FOUND":
                    self._log(f"Туннель '{name}' уже был — переустанавливаю…")
                    run_elevated(wg_exe, ["/uninstalltunnelservice", name])
                    time.sleep(1)

                if self._cancel:
                    self._log("Отменено.")
                    self.root.after(0, self._end_connect, False)
                    return

                code = run_elevated(wg_exe, ["/installtunnelservice", str(conf)])
                if code == 0:
                    self._log("✔ Туннель установлен. Проверяю адаптер…")
                    ok = False
                    for _ in range(60):
                        if self._cancel:
                            break
                        if adapter_state(name) == "UP":
                            ok = True
                            break
                        time.sleep(0.5)
                    if self._cancel:
                        self._log("⏹ Отмена — снимаю туннель…")
                        run_elevated(wg_exe, ["/uninstalltunnelservice", name])
                        self._log("✔ Отменено.")
                        self.root.after(0, self._end_connect, False)
                        return
                    if ok:
                        self._log(f"✔ Туннель '{name}' ПОДНЯТ.")
                        self._show_handshake(name)
                        self._log("Проверьте handshake на сервере:")
                        self._log("  sudo python3 server.py status → 'latest handshake'")
                        self.root.after(0, self._end_connect, True)
                    else:
                        self._log("⚠ Адаптер не поднялся. Попробуйте ещё раз.")
                        self.root.after(0, self._end_connect, False)
                else:
                    self._log(f"[!] wireguard.exe вернул код {code}.")
                    self.root.after(0, self._end_connect, False)
            else:
                self._log("На этой ОС:  sudo python3 client.py up")
                self.root.after(0, self._end_connect, False)
        except Exception as exc:
            self._log(f"[ОШИБКА] {exc}")
            self.root.after(0, self._end_connect, False)

    def _end_connect(self, connected: bool) -> None:
        self._busy = False
        self._cancel = False
        self._set_buttons_enabled(True)
        self._set_phase("connected" if connected else "idle")

    # ---- отключение ----

    def _begin_disconnect(self) -> None:
        if self._busy:
            return
        self._cancel = False
        self._busy = True
        self._set_phase("working")
        threading.Thread(target=self._do_disconnect, daemon=True).start()

    def _do_disconnect(self) -> None:
        try:
            name = self._safe_name()
            wg_exe = find_wireguard_exe()
            if sys.platform.startswith("win") and wg_exe:
                self._log(f"Отключаю туннель '{name}'… (окно UAC)")
                run_elevated(wg_exe, ["/uninstalltunnelservice", name])
                for o in active_wg_tunnels():
                    if self._cancel:
                        break
                    run_elevated(wg_exe, ["/uninstalltunnelservice", o])
                    time.sleep(0.5)
                self._log("✔ Отключено.")
            else:
                self._log("На этой ОС:  sudo python3 client.py down")
        except Exception as exc:
            self._log(f"[ОШИБКА] {exc}")
        finally:
            self.root.after(0, self._end_disconnect)

    def _end_disconnect(self) -> None:
        self._busy = False
        self._cancel = False
        self._set_buttons_enabled(True)
        self._set_phase("idle")

    # ---- статус ----

    def cmd_status(self) -> None:
        name = self._safe_name()
        if sys.platform.startswith("win"):
            st = adapter_state(name)
            up = st == "UP"
            if up:
                self._log(f"✔ Туннель '{name}' ПОДНЯТ.")
            elif st == "NOT_FOUND":
                self._log(f"Туннель '{name}' не поднят.")
            else:
                self._log(f"Адаптер '{name}': {st}")
            self._show_handshake(name)
            # синхронизируем кнопку с реальным состоянием (на главном потоке)
            self.root.after(0, self._apply_status_phase, up)
        else:
            self._log("Статус: sudo python3 client.py status")

    def _apply_status_phase(self, up: bool) -> None:
        if self._busy or self._phase == "working":
            return
        self._set_phase("connected" if up else "idle")

    def _show_handshake(self, name: str) -> None:
        wg = find_wg_exe()
        if not wg:
            return
        try:
            proc = subprocess.run([wg, "show", name], capture_output=True,
                                  text=True, timeout=20, creationflags=_NO_WIN)
            if proc.returncode == 0 and proc.stdout.strip():
                self._log(proc.stdout.strip())
            else:
                self._log("  (детали handshake — от администратора или на сервере)")
        except Exception:
            pass


def main() -> None:
    try:
        import tkinter  # noqa
    except Exception:
        print("tkinter недоступен.")
        sys.exit(1)
    root = tk.Tk()
    ProGVPNClientApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
