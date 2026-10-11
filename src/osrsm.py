import sys
import os
import json
import time
import threading
import queue

import psutil
from PIL import Image, ImageDraw, ImageFont, ImageTk
import pystray
from pystray import MenuItem as item

import tkinter as tk


APP_NAME = "OS-RSM"
APP_VERSION = "1.0s1b"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
CONFIG_PATH = os.path.expanduser("~/.config/tray-monitor.json")

EDIT_ICON_PATH = os.path.join(ASSETS_DIR, "edit.png")
CLEAR_ICON_PATH = os.path.join(ASSETS_DIR, "clear.png")
VOPROS_ICON_PATH = os.path.join(ASSETS_DIR, "vopros.png")

DEFAULT_COLORS = {
    "CPU":  "#59ff00",
    "RAM":  "#c300eb",
    "DISK": "#ffc800",
    "SWAP": "#0080ff",
    "TEMP": "#ff4040",
}


def find_font_file():
    try:
        files = os.listdir(FONTS_DIR)
    except OSError:
        return None
    files = [f for f in files if f.lower().endswith((".ttf", ".otf"))]
    if not files:
        return None
    files.sort()
    return os.path.join(FONTS_DIR, files[0])


FONT_FILE = find_font_file()


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        f = open(CONFIG_PATH, "r", encoding="utf-8")
        try:
            return json.load(f)
        finally:
            f.close()
    except Exception as e:
        print("config broken:", e)
        return {}


def save_config(cfg):
    d = os.path.dirname(CONFIG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    f = open(CONFIG_PATH, "w", encoding="utf-8")
    try:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    finally:
        f.close()


CONFIG = load_config()


# счетчики
def get_cpu():
    return psutil.cpu_percent(interval=None)


def get_ram():
    return psutil.virtual_memory().percent


def get_disk():
    p = "C:\\" if sys.platform == "win32" else "/"
    return psutil.disk_usage(p).percent


def get_swap():
    return psutil.swap_memory().percent


def get_temp_c():
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None
    for name in ("coretemp", "k10temp", "zenpower", "acpitz"):
        if name in temps and temps[name]:
            return temps[name][0].current
    for v in temps.values():
        if v:
            return v[0].current
    return None


METRICS = {
    "CPU":  {"fn": get_cpu,    "color": DEFAULT_COLORS["CPU"]},
    "RAM":  {"fn": get_ram,    "color": DEFAULT_COLORS["RAM"]},
    "DISK": {"fn": get_disk,   "color": DEFAULT_COLORS["DISK"]},
    "SWAP": {"fn": get_swap,   "color": DEFAULT_COLORS["SWAP"]},
    "TEMP": {"fn": get_temp_c, "color": DEFAULT_COLORS["TEMP"]},
}

CUSTOM_KEY = "CUSTOM"
CUSTOM_COLOR = "#7a7a7a"


def get_color(key):
    colors = CONFIG.get("colors", {})
    if key in colors:
        return colors[key]
    if key == CUSTOM_KEY:
        return CUSTOM_COLOR
    return METRICS[key]["color"]


def save_color(key, color):
    CONFIG.setdefault("colors", {})[key] = color
    save_config(CONFIG)


def reset_colors():
    CONFIG["colors"] = {}
    save_config(CONFIG)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


_font_cache = {}
_font_cache_lock = threading.Lock()


def get_font(size):
    f = _font_cache.get(size)
    if f is not None:
        return f

    paths = []
    if FONT_FILE:
        paths.append(FONT_FILE)
    paths.append("DejaVuSans-Bold.ttf")
    paths.append("Arial.ttf")

    font = None
    for p in paths:
        try:
            font = ImageFont.truetype(p, size)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()

    with _font_cache_lock:
        _font_cache[size] = font
    return font


class State:
    def __init__(self):
        cur = CONFIG.get("current", "CPU")
        if cur != CUSTOM_KEY and cur not in METRICS:
            cur = "CPU"
        self.current = cur
        self.custom_text = CONFIG.get("custom_text", "")
        self.temp_unit = CONFIG.get("temp_unit", "C")
        self.font_scale = CONFIG.get("font_scale", 1.0)
        self.values = {}
        for k in METRICS:
            self.values[k] = 0
        self.lock = threading.Lock()


state = State()


def _scaled(n):
    return max(6, int(round(n * state.font_scale)))


def stats_worker():
    psutil.cpu_percent(interval=None)
    while True:
        for name, meta in METRICS.items():
            try:
                v = meta["fn"]()
            except Exception:
                v = None
            with state.lock:
                state.values[name] = v
        time.sleep(1.0)


# иконка в трее
def render_tray_icon():
    with state.lock:
        name = state.current
        custom = state.custom_text
        unit = state.temp_unit
        raw = state.values.get(name, 0)

    if raw is None:
        val = None
    else:
        val = int(round(raw))

    special_temp = False

    if name == CUSTOM_KEY:
        color = hex_to_rgb(get_color(CUSTOM_KEY))
        text = custom if custom else "..."
        if len(text) <= 2:
            f_big = get_font(_scaled(40))
        else:
            f_big = get_font(_scaled(28))
    elif name == "TEMP":
        color = hex_to_rgb(get_color("TEMP"))
        if val is None:
            text = "--"
            f_big = get_font(_scaled(36))
        else:
            t = val
            if unit == "F":
                t = int(round(t * 9 / 5 + 32))
            text = str(t)
            if len(text) <= 2:
                f_big = get_font(_scaled(38))
            else:
                f_big = get_font(_scaled(32))
            special_temp = True
    else:
        color = hex_to_rgb(get_color(name))
        text = str(val)
        if len(text) <= 2:
            f_big = get_font(_scaled(36))
        else:
            f_big = get_font(_scaled(30))

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((2, 2, size - 2, size - 2), radius=10, fill=color)

    if lum(color) > 140:
        fg = (0, 0, 0)
    else:
        fg = (255, 255, 255)

    if special_temp:
        bbox = d.textbbox((0, 0), text, font=f_big)
        tw = bbox[2] - bbox[0]
        d.text(((size - tw) / 2 - bbox[0], 8 - bbox[1]),
               text, font=f_big, fill=fg)
        deg = "C" if unit == "C" else "F"
        f_deg = get_font(_scaled(22))
        bb = d.textbbox((0, 0), deg, font=f_deg)
        dw = bb[2] - bb[0]
        d.text(((size - dw) / 2 - bb[0], size - 24 - bb[1]),
               deg, font=f_deg, fill=fg)
    else:
        bbox = d.textbbox((0, 0), text, font=f_big)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        d.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
               text, font=f_big, fill=fg)

    return img


ui_queue = queue.Queue()


def build_tray():
    def on_open(icon, item_):
        ui_queue.put("open_window")

    def on_quit(icon, item_):
        icon.stop()
        ui_queue.put("quit")

    icon = pystray.Icon(
        name="sysmon",
        icon=render_tray_icon(),
        title=APP_NAME + " v" + APP_VERSION,
        menu=pystray.Menu(
            item("Open", on_open, default=True),
            item("Quit", on_quit),
        ),
    )

    _tray_cache = {"key": None}

    def refresh():
        while True:
            try:
                with state.lock:
                    key = (state.current, state.values.get(state.current),
                           state.custom_text, state.temp_unit,
                           tuple(CONFIG.get("colors", {}).items()))
                if key != _tray_cache["key"]:
                    _tray_cache["key"] = key
                    icon.icon = render_tray_icon()
            except Exception as e:
                print("tray refresh:", e)
            time.sleep(1.0)

    t = threading.Thread(target=refresh, daemon=True)
    t.start()
    return icon


BG = "#1e1e1e"
TEXT_MUTED = "#777777"
PANEL = "#2a2a33"

TILE_SIZE = 96
TILE_RENDER_SIZE = 192

TILE_FONT_VALUE = 72
TILE_FONT_LABEL = 22


def render_tile_image(color_hex, value_text, label_text,
                      hovered, pressed, active,
                      wave_r=None, wave_alpha=0):
    s = TILE_RENDER_SIZE
    img = Image.new("RGB", (s, s), color_hex)
    d = ImageDraw.Draw(img)

    rgb = hex_to_rgb(color_hex)
    if lum(rgb) > 140:
        fg = (0, 0, 0)
        wave_color = (0, 0, 0)
    else:
        fg = (255, 255, 255)
        wave_color = (255, 255, 255)

    if active:
        d.rectangle((0, 0, s, 8), fill=(255, 255, 255))

    f_val = get_font(_scaled(TILE_FONT_VALUE))
    bbox = d.textbbox((0, 0), value_text, font=f_val)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    d.text(((s - tw) / 2 - bbox[0], s / 2 - th / 2 - bbox[1] - 10),
           value_text, font=f_val, fill=fg)

    f_lbl = get_font(_scaled(TILE_FONT_LABEL))
    bbox2 = d.textbbox((0, 0), label_text, font=f_lbl)
    lw = bbox2[2] - bbox2[0]
    d.text(((s - lw) / 2 - bbox2[0], s - 40),
           label_text, font=f_lbl, fill=fg)

    if wave_r is not None and wave_alpha > 0:
        overlay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        cx = s // 2
        cy = s // 2
        r = int(wave_r)
        thick = max(2, int(s * 0.10))
        bbox_ring = (cx - r, cy - r, cx + r, cy + r)
        od.ellipse(bbox_ring,
                   outline=wave_color + (wave_alpha,),
                   width=thick)
        img = Image.alpha_composite(img.convert("RGBA"),
                                    overlay).convert("RGB")

    img = img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)

    if hovered or pressed:
        step = 5 if not pressed else 3
        dot = (0, 0, 0) if lum(rgb) > 140 else (255, 255, 255)
        alpha = 600
        overlay = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(0, TILE_SIZE, step):
            for x in range(0, TILE_SIZE, step):
                od.point((x, y), fill=dot + (alpha,))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    return img


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} PB"

class ColorSlider(tk.Canvas):
    W = 200
    H = 24
    PAD = 12
    KNOB_R = 9
    DOT_R = 4

    def __init__(self, parent, color_hex, initial, on_change):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=BG, highlightthickness=0, bd=0)
        self.color_hex = color_hex
        self.value = int(initial)
        self.on_change = on_change

        self.bind("<Button-1>", self._on_click)
        self.bind("<B1-Motion>", self._on_drag)
        self._draw()

    def _val_to_x(self, v):
        return self.PAD + (self.W - 2 * self.PAD) * (v / 255.0)

    def _x_to_val(self, x):
        x = max(self.PAD, min(self.W - self.PAD, x))
        v = (x - self.PAD) / (self.W - 2 * self.PAD) * 255
        return int(round(v))

    def _draw(self):
        self.delete("all")
        y = self.H // 2

        self.create_line(self.PAD, y, self.W - self.PAD, y,
                         fill="#444444", width=3, capstyle="round")

        x = self._val_to_x(self.value)
        self.create_line(self.PAD, y, x, y,
                         fill=self.color_hex, width=3, capstyle="round")

        self.create_oval(x - self.KNOB_R, y - self.KNOB_R,
                         x + self.KNOB_R, y + self.KNOB_R,
                         fill="#ffffff", outline="")

    def set_value(self, v):
        self.value = int(v)
        self._draw()

    def _on_click(self, e):
        self.value = self._x_to_val(e.x)
        self._draw()
        self.on_change(self.value)

    def _on_drag(self, e):
        self.value = self._x_to_val(e.x)
        self._draw()
        self.on_change(self.value)

class PaletteWindow:
    def __init__(self, root, on_live_preview, on_apply):
        self.root = root
        self.win = None
        self.key = None
        self.on_live_preview = on_live_preview
        self.on_apply = on_apply
        self.original_color = None
        self.preview_color = None
        self.sliders = {}
        self.swatch = None
        self.hex_lbl = None

    def show(self, key, current_hex):
        if self.win is not None and self.win.winfo_exists():
            if self.key == key:
                self.win.deiconify()
                self.win.lift()
                self.win.focus_force()
                return
            self._cancel()

        self.key = key
        self.original_color = current_hex
        self.preview_color = current_hex
        self.sliders = {}

        self.win = tk.Toplevel(self.root)
        self.win.title(f"Color: {key}")
        self.win.configure(bg=BG)
        self.win.geometry("280x250")
        self.win.resizable(False, False)

        rgb = hex_to_rgb(current_hex)

        self.swatch = tk.Label(self.win, bg=current_hex, text="",
                               width=28, height=2)
        self.swatch.pack(pady=(10, 4))

        self.hex_lbl = tk.Label(self.win, text=current_hex,
                                bg=BG, fg="#cccccc",
                                font=("Sans", 10, "bold"))
        self.hex_lbl.pack()

        for name, color, init in (
            ("R", "#ff0000", rgb[0]),
            ("G", "#00ff00", rgb[1]),
            ("B", "#0000ff", rgb[2]),
        ):
            row = tk.Frame(self.win, bg=BG)
            row.pack(fill="x", padx=14, pady=1)

            tk.Label(row, text=name, bg=BG, fg=color,
                     font=("Sans", 10, "bold"), width=2
                     ).pack(side="left")

            slider = ColorSlider(
                row, color_hex=color, initial=init,
                on_change=lambda v, n=name: self._on_change(n, v)
            )
            slider.pack(side="left", padx=(6, 0))
            self.sliders[name] = slider

        btns = tk.Frame(self.win, bg=BG)
        btns.pack(pady=(10, 8))

        ok = tk.Label(btns, text="Apply", bg="#2a2a33", fg="#ffffff",
                      font=("Sans", 9, "bold"), padx=16, pady=5,
                      cursor="hand2")
        ok.pack(side="left", padx=4)
        ok.bind("<Button-1>", lambda e: self._apply())

        cancel = tk.Label(btns, text="Cancel", bg="#2a2a33", fg="#cccccc",
                          font=("Sans", 9), padx=16, pady=5,
                          cursor="hand2")
        cancel.pack(side="left", padx=4)
        cancel.bind("<Button-1>", lambda e: self._cancel())

        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

    def _on_change(self, name, value):
        r = self.sliders["R"].value
        g = self.sliders["G"].value
        b = self.sliders["B"].value
        hex_color = rgb_to_hex((r, g, b))
        self.preview_color = hex_color
        self.swatch.config(bg=hex_color)
        self.hex_lbl.config(text=hex_color)
        self.on_live_preview(self.key, hex_color)

    def _apply(self):
        if self.key and self.preview_color:
            self.on_apply(self.key, self.preview_color)
        self._close()

    def _cancel(self):
        if self.key and self.original_color:
            self.on_live_preview(self.key, self.original_color)
        self._close()

    def _close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
        self.key = None
        self.sliders = {}

class DiskDetailWindow:
    def __init__(self, root):
        self.root = root
        self.win = None
        self.label = None
        self.bar = None
        self.info = None
        self.mountpoint = None

    def show(self, mountpoint):
        self.mountpoint = mountpoint

        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self._refresh()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("")
        self.win.configure(bg=BG)
        self.win.geometry("280x110")
        self.win.resizable(False, False)

        self.bar = tk.Canvas(self.win, width=240, height=16,
                             bg=PANEL, highlightthickness=0, bd=0)
        self.bar.pack(pady=(14, 6))

        self.label = tk.Label(self.win, text="", bg=BG, fg="#ffffff",
                              font=("Sans", 12, "bold"))
        self.label.pack()

        self.info = tk.Label(self.win, text="", bg=BG, fg="#aaaaaa",
                             font=("Sans", 8), justify="center")
        self.info.pack(pady=(4, 0))

        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()

    def _refresh(self):
        try:
            usage = psutil.disk_usage(self.mountpoint)
        except Exception:
            return

        percent = usage.percent
        used = usage.used
        free = usage.free
        total = usage.total

        self.bar.delete("all")
        w = 240
        h = 16
        fill_w = int(w * percent / 100)

        if percent < 60:
            color = "#59ff00"
        elif percent < 85:
            color = "#ffc800"
        else:
            color = "#ff4040"

        self.bar.create_rectangle(0, 0, fill_w, h, fill=color, outline="")
        self.label.config(text=f"{percent:.0f}%")
        self.info.config(
            text=f"Used: {human_bytes(used)}   "
                 f"Free: {human_bytes(free)}   "
                 f"Total: {human_bytes(total)}"
        )

    def _on_close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None

class HelpWindow:
    def __init__(self, root):
        self.root = root
        self.win = None

    def show(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("Help")
        self.win.configure(bg=BG)
        self.win.geometry("420x340")
        self.win.resizable(False, False)

        text = (
            f"{APP_NAME} - open source system resource monitor\n\n"
            "Controls:\n"
            "* Left click on tile: set active metric in tray\n"
            "* Left click on TEMP: switch C / F\n"
            "* Left click on CUSTOM: set custom text (up to 3 chars)\n"
            "* Double click on DISK: disk details\n"
            "* Click on pencil: change metric color\n"
            "* Mouse wheel: font size\n"
            "* Click on tray icon: open window\n"
            "Author: M1han\n"
            "github.com/MihanPROJECTS/OS-RSM"
        )

        tk.Label(self.win, text=text, bg=BG, fg="#cccccc",
                 font=("Sans", 9), justify="left").pack(padx=20, pady=20)

        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None

class Tile:
    SIZE = TILE_SIZE

    WAVE_DURATION_MS = 400
    WAVE_FPS = 60
    WAVE_MAX_R = int(TILE_RENDER_SIZE * 0.85)

    def __init__(self, parent, key, get_color_fn, on_click, on_double,
                 on_edit, get_display, is_active):
        self.parent = parent
        self.key = key
        self.get_color_fn = get_color_fn
        self.on_click = on_click
        self.on_double = on_double
        self.on_edit = on_edit
        self.get_display = get_display
        self.is_active = is_active

        self.canvas = tk.Canvas(
            parent, width=self.SIZE, height=self.SIZE,
            bg=BG, highlightthickness=0, bd=0
        )
        self.hovered = False
        self.pressed = False
        self._photo = None
        self._last_render_key = None

        self._wave_start = 0.0
        self._wave_active = False
        self._wave_job = None

        self._edit_icon_img = None
        try:
            pil = Image.open(EDIT_ICON_PATH).convert("RGBA")
            pil = pil.resize((20, 20), Image.LANCZOS)
            self._edit_icon_img = ImageTk.PhotoImage(pil)
        except Exception as e:
            print("edit icon not loaded:", e)

        self._draw(force=True)

        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)

    def _render_key(self):
        value_text, label = self.get_display()
        color = self.get_color_fn(self.key)
        return (value_text, label, color,
                self.hovered, self.pressed, self.is_active())

    def _draw(self, force=False, wave_r=None, wave_alpha=0):
        base_key = self._render_key()
        if wave_r is not None and wave_alpha > 0:
            key = base_key + ("wave", int(wave_r), int(wave_alpha))
        else:
            key = base_key
        if not force and key == self._last_render_key:
            return
        self._last_render_key = key

        value_text, label, color = base_key[0], base_key[1], base_key[2]
        img = render_tile_image(
            color, value_text, label,
            self.hovered, self.pressed, self.is_active(),
            wave_r=wave_r, wave_alpha=wave_alpha
        )
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

        if self._edit_icon_img is not None:
            pad = 4
            box = 22
            x0 = self.SIZE - pad - box
            y0 = pad
            self.canvas.create_rectangle(
                x0, y0, x0 + box, y0 + box,
                fill="#000000", outline="",
                stipple="gray50",
                tags="edit_bg"
            )
            self.canvas.create_image(
                self.SIZE - pad, pad,
                anchor="ne", image=self._edit_icon_img,
                tags="edit_icon"
            )
            self.canvas.tag_bind("edit_icon", "<Button-1>",
                                 lambda e: self._on_edit_click())
            self.canvas.tag_bind("edit_bg", "<Button-1>",
                                 lambda e: self._on_edit_click())

    def _on_edit_click(self):
        if self.on_edit:
            self.on_edit(self.key)

    def refresh(self):
        if self._wave_active:
            return
        self._draw()

    def _start_wave(self):
        self._wave_start = time.time()
        self._wave_active = True
        if self._wave_job is not None:
            try:
                self.canvas.after_cancel(self._wave_job)
            except Exception:
                pass
        self._tick_wave()

    def _tick_wave(self):
        if not self._wave_active:
            return
        elapsed = (time.time() - self._wave_start) * 1000.0
        t = elapsed / self.WAVE_DURATION_MS
        if t >= 1.0:
            self._wave_active = False
            self._wave_job = None
            self._draw(force=True)
            return

        ease = 1.0 - (1.0 - t) ** 2
        r = self.WAVE_MAX_R * ease
        alpha = int(180 * (1.0 - t))

        self._draw(force=True, wave_r=r, wave_alpha=alpha)

        dt = int(1000.0 / self.WAVE_FPS)
        self._wave_job = self.canvas.after(dt, self._tick_wave)

    def _on_enter(self, e):
        self.hovered = True
        self._draw()
        self.canvas.configure(cursor="hand2")

    def _on_leave(self, e):
        self.hovered = False
        self.pressed = False
        self._draw()

    def _on_press(self, e):
        self.pressed = True
        self._draw()

    def _on_release(self, e):
        was = self.pressed
        self.pressed = False
        self._draw()
        if was:
            self._start_wave()
            if self.on_click:
                self.on_click()

    def _on_double(self, e):
        if self.on_double:
            self.on_double()

class ConfigWindow:
    COLS = 3
    GAP = 8

    def __init__(self, root):
        self.root = root
        root.title(APP_NAME + " v" + APP_VERSION)

        TILE = TILE_SIZE
        GAP = self.GAP
        PAD = 16
        VER_H = 32

        w = 3 * TILE + 2 * GAP + 2 * PAD
        h = 3 * TILE + 2 * GAP + PAD + VER_H
        root.geometry(str(w) + "x" + str(h))
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        self.tiles = {}
        self.custom_editing = False
        self._wheel_pending = False
        self._wheel_dir = 0

        self.disk_detail = DiskDetailWindow(root)
        self.help_window = HelpWindow(root)
        self.palette = PaletteWindow(
            root,
            on_live_preview=self._live_preview,
            on_apply=self._apply_color,
        )

        self.grid_frame = tk.Frame(root, bg=BG)
        self.grid_frame.pack(padx=PAD, pady=(PAD, PAD + VER_H))

        bottom = tk.Frame(root, bg=BG)
        bottom.place(relx=0.0, rely=1.0, anchor="sw", x=10, y=-6)

        # Кнопка ? картинка vopros.png, можно заменить, если компилировать исходники
        self.help_btn_img = None
        try:
            pil = Image.open(VOPROS_ICON_PATH).convert("RGBA")
            pil = pil.resize((18, 18), Image.LANCZOS)
            self.help_btn_img = ImageTk.PhotoImage(pil)
        except Exception as e:
            print("vopros icon not loaded:", e)

        if self.help_btn_img is not None:
            self.help_btn = tk.Label(
                bottom, bg=BG, image=self.help_btn_img,
                cursor="hand2", padx=2
            )
            self.help_btn.pack(side="left")
            self.help_btn.bind("<Button-1>", lambda e: self.help_window.show())
        else:
            self.help_btn = tk.Label(
                bottom, text="?", bg=BG, fg="#cccccc",
                font=("Sans", 11, "bold"), width=2, height=1,
                cursor="hand2"
            )
            self.help_btn.pack(side="left")
            self.help_btn.bind("<Button-1>", lambda e: self.help_window.show())

        self.clear_btn_img = None
        try:
            pil = Image.open(CLEAR_ICON_PATH).convert("RGBA")
            pil = pil.resize((18, 18), Image.LANCZOS)
            self.clear_btn_img = ImageTk.PhotoImage(pil)
        except Exception as e:
            print("clear icon not loaded:", e)

        if self.clear_btn_img is not None:
            self.clear_btn = tk.Label(
                bottom, bg=BG, image=self.clear_btn_img,
                cursor="hand2", padx=2
            )
            self.clear_btn.pack(side="left", padx=(4, 0))
            self.clear_btn.bind("<Button-1>", lambda e: self._clear_colors())
        else:
            self.clear_btn = tk.Label(
                bottom, text="x", bg=BG, fg="#cccccc",
                font=("Sans", 10, "bold"), width=2, cursor="hand2"
            )
            self.clear_btn.pack(side="left", padx=(4, 0))
            self.clear_btn.bind("<Button-1>", lambda e: self._clear_colors())

        # версия название
        tk.Label(root, text="v" + APP_VERSION,
                 bg=BG, fg=TEXT_MUTED, font=("Sans", 7)
                 ).place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)

        root.bind("<MouseWheel>", self._on_wheel)
        root.bind("<Button-4>", lambda e: self._schedule_wheel(1))
        root.bind("<Button-5>", lambda e: self._schedule_wheel(-1))

        self.build_ui()
        self._bind_global_click()
        self.refresh_loop()

    def _on_wheel(self, event):
        self._schedule_wheel(1 if event.delta > 0 else -1)

    def _schedule_wheel(self, direction):
        self._wheel_dir = direction
        if self._wheel_pending:
            return
        self._wheel_pending = True
        self.root.after(30, self._apply_wheel)

    def _apply_wheel(self):
        self._wheel_pending = False
        direction = self._wheel_dir
        if direction == 0:
            return
        self._wheel_dir = 0
        step = 0.1
        s = state.font_scale + direction * step
        s = max(0.7, min(1.5, round(s, 2)))
        if s == state.font_scale:
            return
        state.font_scale = s
        CONFIG["font_scale"] = s
        save_config(CONFIG)
        self._redraw_all()

    def _clear_colors(self):
        reset_colors()
        self._redraw_all()

    def _bind_global_click(self):
        self.root.bind("<Button-1>", self._on_global_click, add="+")
        self.root.bind("<Return>", lambda e: self._finish_custom(), add="+")
        self.root.bind("<Escape>", lambda e: self._finish_custom(), add="+")
        self.root.bind("<Key>", self._on_key, add="+")

    def _on_global_click(self, event):
        if not self.custom_editing:
            return
        custom_tile = self.tiles.get(CUSTOM_KEY)
        if custom_tile and event.widget is custom_tile.canvas:
            return
        self._finish_custom()

    def _on_key(self, event):
        if not self.custom_editing:
            return

        if event.keysym == "BackSpace":
            state.custom_text = state.custom_text[:-1]
        elif event.keysym in ("Return", "Escape"):
            self._finish_custom()
            return
        elif event.char and len(state.custom_text) < 3:
            ch = event.char
            if ch.isprintable() and ch != " ":
                state.custom_text = (state.custom_text + ch)[:3]
        else:
            return

        CONFIG["custom_text"] = state.custom_text
        save_config(CONFIG)
        tile = self.tiles.get(CUSTOM_KEY)
        if tile:
            tile._draw(force=True)

    def _finish_custom(self):
        if not self.custom_editing:
            return
        self.custom_editing = False
        CONFIG["custom_text"] = state.custom_text
        CONFIG["current"] = CUSTOM_KEY
        save_config(CONFIG)
        state.current = CUSTOM_KEY
        self._redraw_all()

    def _disk_mountpoint(self):
        return "C:\\" if sys.platform == "win32" else "/"

    def build_ui(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles.clear()

        keys = list(METRICS.keys()) + [CUSTOM_KEY]

        for idx, k in enumerate(keys):
            r = idx // 3
            c = idx % 3

            on_double = None
            if k == "DISK":
                mp = self._disk_mountpoint()
                on_double = lambda m=mp: self.disk_detail.show(m)

            tile = Tile(
                self.grid_frame, k,
                get_color_fn=get_color,
                on_click=lambda kk=k: self.pick(kk),
                on_double=on_double,
                on_edit=self._open_palette,
                get_display=self._display_for(k),
                is_active=lambda kk=k: state.current == kk
            )
            tile.canvas.grid(row=r, column=c,
                             padx=self.GAP // 2, pady=self.GAP // 2)
            self.tiles[k] = tile

    def _open_palette(self, key):
        current = get_color(key)
        self.palette.show(key, current)

    def _live_preview(self, key, color):
        CONFIG.setdefault("colors", {})[key] = color
        tile = self.tiles.get(key)
        if tile:
            tile._draw(force=True)

    def _apply_color(self, key, color):
        save_color(key, color)
        tile = self.tiles.get(key)
        if tile:
            tile._draw(force=True)

    def _display_for(self, key):
        def fn():
            with state.lock:
                custom = state.custom_text
                raw = state.values.get(key, 0)
                unit = state.temp_unit

            if key == CUSTOM_KEY:
                if custom:
                    return custom, "CUSTOM"
                return "...", "CUSTOM"

            if key == "TEMP":
                if raw is None:
                    return "--", "TEMP"
                t = int(round(raw))
                if unit == "F":
                    t = int(round(t * 9 / 5 + 32))
                    return str(t), "TEMP F"
                return str(t), "TEMP C"

            return str(int(round(raw))), key
        return fn

    def pick(self, key):
        if key == CUSTOM_KEY:
            self.custom_editing = True
            state.custom_text = ""
            CONFIG["custom_text"] = ""
            save_config(CONFIG)
            state.current = CUSTOM_KEY
            CONFIG["current"] = CUSTOM_KEY
            save_config(CONFIG)
            self._redraw_all()
            return

        if key == "TEMP":
            if state.current != "TEMP":
                state.current = "TEMP"
                CONFIG["current"] = "TEMP"
                save_config(CONFIG)
            if state.temp_unit == "C":
                state.temp_unit = "F"
            else:
                state.temp_unit = "C"
            CONFIG["temp_unit"] = state.temp_unit
            save_config(CONFIG)
            self._redraw_all()
            return

        self._set_metric(key)

    def _set_metric(self, key):
        state.current = key
        CONFIG["current"] = key
        save_config(CONFIG)
        self._redraw_all()

    def _redraw_all(self):
        for tile in self.tiles.values():
            tile._draw(force=True)

    def refresh_loop(self):
        for tile in self.tiles.values():
            tile.refresh()
        self.root.after(700, self.refresh_loop)


def ui_pump(root, icon):
    try:
        while True:
            cmd = ui_queue.get_nowait()
            if cmd == "open_window":
                root.deiconify()
                root.lift()
                root.focus_force()
            elif cmd == "quit":
                icon.stop()
                root.quit()
                root.destroy()
                return
    except queue.Empty:
        pass
    root.after(200, lambda: ui_pump(root, icon))


def main():
    print("[font] using:", FONT_FILE or "system fallback")
    print("[edit icon]:", EDIT_ICON_PATH if os.path.exists(EDIT_ICON_PATH) else "not found")
    print("[clear icon]:", CLEAR_ICON_PATH if os.path.exists(CLEAR_ICON_PATH) else "not found")
    print("[vopros icon]:", VOPROS_ICON_PATH if os.path.exists(VOPROS_ICON_PATH) else "not found")

    t = threading.Thread(target=stats_worker, daemon=True)
    t.start()

    icon = build_tray()
    threading.Thread(target=icon.run, daemon=True).start()

    root = tk.Tk()
    ConfigWindow(root)
    root.withdraw()

    ui_pump(root, icon)
    root.mainloop()


if __name__ == "__main__":
    main()
