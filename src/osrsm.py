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
from tkinter import filedialog


APP_NAME = "OS-RSM"
APP_VERSION = "2.0r"

if getattr(sys, "frozen", False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

FONTS_DIR = os.path.join(BASE_DIR, "fonts")
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
CONFIG_PATH = os.path.expanduser("~/.config/tray-monitor.json")
BG_IMAGE_PATH = os.path.expanduser("~/.config/tray-monitor/background.png")

EDIT_ICON_PATH = os.path.join(ASSETS_DIR, "edit.png")
CLEAR_ICON_PATH = os.path.join(ASSETS_DIR, "clear.png")
VOPROS_ICON_PATH = os.path.join(ASSETS_DIR, "vopros.png")
PALL_ICON_PATH = os.path.join(ASSETS_DIR, "pall.png")
WALL_PATH = os.path.join(ASSETS_DIR, "wall.png")

DEFAULT_COLORS = {
    "CPU":  "#ff3030",
    "RAM":  "#30ff30",
    "DISK": "#3030ff",
    "SWAP": "#ffcc00",
    "TEMP": "#ff30ff",
}

CUSTOM_KEY = "CUSTOM"
CUSTOM_COLOR = "#7a7a7a"

ALL_KEYS = ["CPU", "RAM", "DISK", "SWAP", "TEMP"]

BG = "#0a0a0a"
PANEL = "#1f1f1f"
PANEL_HOVER = "#2a2a2a"
PANEL_PRESS = "#0f0f0f"
DIVIDER = "#2f2f2f"
TEXT_MUTED = "#6b6b6b"
TEXT = "#e0e0e0"

DEFAULT_ACCENT = "#0078d4"

UI_FONT = "Segoe UI"
UI_FONT_BOLD = "Segoe UI Semibold"

SQUARE_BORDER_WIDTH = 2


_bg_source = None
_bg_source_path = None


def load_background():
    global _bg_source, _bg_source_path
    _bg_source = None
    _bg_source_path = None
    return False


def has_background():
    return False


def tile_uses_bg(key):
    return False


def get_bg_piece(row, col, cols, rows, size):
    return None


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
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("config broken:", e)
        return {}


def save_config(cfg):
    d = os.path.dirname(CONFIG_PATH)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("save_config:", e)


CONFIG = load_config()
load_background()


def get_accent():
    return CONFIG.get("accent_color", DEFAULT_ACCENT)


def save_accent(color):
    CONFIG["accent_color"] = color
    save_config(CONFIG)


def get_effect():
    e = CONFIG.get("palette_effect", "cascade")
    if e not in ("cascade", "left", "right", "top", "bottom"):
        e = "cascade"
    return e


def save_effect(e):
    CONFIG["palette_effect"] = e
    save_config(CONFIG)


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def rgb_to_hex(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def lighten(hex_color, amount):
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex((
        min(255, int(r + (255 - r) * amount)),
        min(255, int(g + (255 - g) * amount)),
        min(255, int(b + (255 - b) * amount)),
    ))


def darken(hex_color, amount):
    r, g, b = hex_to_rgb(hex_color)
    return rgb_to_hex((
        max(0, int(r * (1 - amount))),
        max(0, int(g * (1 - amount))),
        max(0, int(b * (1 - amount))),
    ))


def accent_fg(accent_hex):
    try:
        rgb = hex_to_rgb(accent_hex)
    except Exception:
        return "#ffffff"
    return "#000000" if lum(rgb) > 150 else "#ffffff"


def blend(hex_a, hex_b, t):
    ra, ga, ba = hex_to_rgb(hex_a)
    rb, gb, bb = hex_to_rgb(hex_b)
    return rgb_to_hex((
        int(ra + (rb - ra) * t),
        int(ga + (gb - ga) * t),
        int(ba + (bb - ba) * t),
    ))


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


def ensure_order_in_config():
    if "tile_order" not in CONFIG or not isinstance(CONFIG["tile_order"], list):
        order = list(ALL_KEYS)
    else:
        seen = set()
        cleaned = []
        for k in CONFIG["tile_order"]:
            if k in ALL_KEYS and k not in seen:
                cleaned.append(k)
                seen.add(k)
        order = cleaned

    if CUSTOM_KEY in order:
        order.remove(CUSTOM_KEY)
    order.append(CUSTOM_KEY)
    CONFIG["tile_order"] = order

    if "hidden_tiles" not in CONFIG or not isinstance(CONFIG["hidden_tiles"], list):
        CONFIG["hidden_tiles"] = []

    save_config(CONFIG)


ensure_order_in_config()


def get_color(key):
    colors = CONFIG.get("colors", {})
    if key in colors:
        return colors[key]
    if key == CUSTOM_KEY:
        return CUSTOM_COLOR
    if key in METRICS:
        return METRICS[key]["color"]
    return "#7a7a7a"


def save_color(key, color):
    CONFIG.setdefault("colors", {})[key] = color
    save_config(CONFIG)


def set_no_bg(key, no_bg):
    pass


RESET_COLOR = DEFAULT_ACCENT


def reset_colors():
    CONFIG["colors"] = {}
    CONFIG["accent_color"] = RESET_COLOR
    for k in list(CONFIG.get("tile_order", [])):
        CONFIG["colors"][k] = RESET_COLOR
    save_config(CONFIG)


def tint_icon(path, color, size):
    try:
        size = int(size)
        if size < 1:
            size = 1
        img = Image.open(path).convert("RGBA")
        img = img.resize((size, size), Image.LANCZOS)
        r, g, b, a = img.split()
        solid = Image.new("RGBA", img.size, color)
        solid.putalpha(a)
        return ImageTk.PhotoImage(solid)
    except Exception as e:
        print("tint_icon:", e)
        return None


def icon_color_for_accent(accent_hex):
    return accent_fg(accent_hex)


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


def render_tray_icon():
    with state.lock:
        name = state.current
        custom = state.custom_text
        unit = state.temp_unit
        raw = state.values.get(name, 0)

    val = None if raw is None else int(round(raw))
    special_temp = False

    if name == CUSTOM_KEY:
        color = hex_to_rgb(get_color(CUSTOM_KEY))
        text = custom if custom else "..."
        f_big = get_font(_scaled(40 if len(text) <= 2 else 28))
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
            f_big = get_font(_scaled(38 if len(text) <= 2 else 32))
            special_temp = True
    else:
        color = hex_to_rgb(get_color(name))
        text = str(val)
        f_big = get_font(_scaled(36 if len(text) <= 2 else 30))

    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    d.rounded_rectangle((2, 2, size - 2, size - 2), radius=0, fill=color)

    fg = (0, 0, 0) if lum(color) > 140 else (255, 255, 255)

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
        try:
            icon.stop()
        except Exception:
            pass
        ui_queue.put("quit")
        threading.Timer(0.2, lambda: os._exit(0)).start()

    icon = pystray.Icon(
        name="sysmon",
        icon=render_tray_icon(),
        title=APP_NAME + " v" + APP_VERSION,
        menu=pystray.Menu(
            item("Open", on_open, default=True),
            item("EXIT", on_quit),
        ),
    )

    _cache = {"key": None}

    def refresh():
        while True:
            try:
                with state.lock:
                    key = (state.current, state.values.get(state.current),
                           state.custom_text, state.temp_unit,
                           tuple(CONFIG.get("colors", {}).items()),
                           get_accent())
                if key != _cache["key"]:
                    _cache["key"] = key
                    icon.icon = render_tray_icon()
            except Exception as e:
                print("tray refresh:", e)
            time.sleep(1.0)

    threading.Thread(target=refresh, daemon=True).start()
    return icon


TILE_SIZE = 96
TILE_RENDER_SIZE = 192

TILE_FONT_VALUE = 72
TILE_FONT_LABEL = 22

_tile_bg_cache = {}
_tile_bg_cache_lock = threading.Lock()


def _get_tile_bg(color_hex, hovered, active, fg_is_light):
    cache_key = (color_hex, hovered, active, fg_is_light)
    with _tile_bg_cache_lock:
        cached = _tile_bg_cache.get(cache_key)
    if cached is not None:
        return cached

    s = TILE_RENDER_SIZE
    img = Image.new("RGB", (s, s), color_hex)

    if hovered and not active:
        if not fg_is_light:
            overlay = Image.new("RGBA", (s, s), (255, 255, 255, 40))
        else:
            overlay = Image.new("RGBA", (s, s), (0, 0, 0, 40))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    if active:
        base_rgb = hex_to_rgb(color_hex)
        base_lum = lum(base_rgb)
        if base_lum < 128:
            factor = 1.55
            new_rgb = tuple(min(255, int(c * factor)) for c in base_rgb)
        else:
            factor = 0.55
            new_rgb = tuple(max(0, int(c * factor)) for c in base_rgb)
        new_hex = rgb_to_hex(new_rgb)
        overlay = Image.new("RGB", (s, s), new_hex)
        img = Image.blend(img, overlay, 0.60)

    with _tile_bg_cache_lock:
        if len(_tile_bg_cache) > 200:
            _tile_bg_cache.clear()
        _tile_bg_cache[cache_key] = img
    return img


def render_tile_image(color_hex, value_text, label_text,
                      hovered, active,
                      font_scale):
    s = TILE_RENDER_SIZE

    base_rgb = hex_to_rgb(color_hex)
    fg_is_light = lum(base_rgb) > 128
    fg = (0, 0, 0) if fg_is_light else (255, 255, 255)

    bg = _get_tile_bg(color_hex, hovered, active, fg_is_light)
    img = bg.copy()
    d = ImageDraw.Draw(img)

    pad_x = int(s * 0.06)
    pad_y = int(s * 0.06)
    bottom_pad = int(s * 0.08)

    def _scaled_local(n):
        return max(6, int(round(n * font_scale)))

    f_val_size = TILE_FONT_VALUE + 30
    f_val = get_font(_scaled_local(f_val_size))
    while f_val_size > 40:
        bb = d.textbbox((0, 0), value_text, font=f_val)
        if bb[2] - bb[0] <= s - pad_x * 2:
            break
        f_val_size -= 4
        f_val = get_font(_scaled_local(f_val_size))

    bbox = d.textbbox((0, 0), value_text, font=f_val)
    d.text((pad_x - bbox[0], pad_y - bbox[1]),
           value_text, font=f_val, fill=fg)

    f_lbl = get_font(_scaled_local(TILE_FONT_LABEL))
    label_upper = label_text.upper()
    bbox2 = d.textbbox((0, 0), label_upper, font=f_lbl)
    lh = bbox2[3] - bbox2[1]
    y_lbl = s - bottom_pad - lh - bbox2[1]
    d.text((pad_x - bbox2[0], y_lbl),
           label_upper, font=f_lbl, fill=fg)

    img = img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)
    return img


def human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def fade_in(win, duration_ms=100, steps=5):
    try:
        win.attributes("-alpha", 0.0)
    except Exception:
        return

    def step(i):
        if not win.winfo_exists():
            return
        try:
            win.attributes("-alpha", i / steps)
        except Exception:
            return
        if i < steps:
            win.after(max(1, duration_ms // steps),
                      lambda: step(i + 1))

    step(0)


def animate_cells_cascade(cells, cell_w, cell_h, cols,
                          delay_ms=15, duration_ms=100, steps=5):
    if not cells:
        return

    gap = 2

    def reveal(idx):
        if idx >= len(cells):
            return
        cell = cells[idx]
        try:
            if not cell.winfo_exists():
                return
        except Exception:
            return

        r = idx // cols
        c = idx % cols
        x = c * (cell_w + gap) + gap // 2
        y = r * (cell_h + gap) + gap // 2

        def grow(s):
            if not cell.winfo_exists():
                return
            t = s / steps
            e = 1 - (1 - t) ** 2
            w = max(1, int(cell_w * e))
            h = max(1, int(cell_h * e))
            cx = x + (cell_w - w) // 2
            cy = y + (cell_h - h) // 2
            try:
                cell.place_configure(x=cx, y=cy, width=w, height=h)
            except Exception:
                return
            if s < steps:
                cell.after(max(1, duration_ms // steps),
                           lambda: grow(s + 1))

        grow(1)
        cell.after(delay_ms, lambda: reveal(idx + 1))

    reveal(0)


def animate_cells_book(cells, cell_w, cell_h, cols, direction,
                       duration_ms=220, steps=10):
    if not cells:
        return

    gap = 2

    def make_grower(idx, base_x, base_y):
        def grow(s):
            if idx >= len(cells):
                return
            try:
                cell = cells[idx]
                if not cell.winfo_exists():
                    return
            except Exception:
                return

            t = s / steps
            e = 1 - (1 - t) ** 3

            try:
                if direction == "left":
                    w = max(1, int(cell_w * e))
                    cell.place_configure(x=base_x, y=base_y,
                                         width=w, height=cell_h)
                elif direction == "right":
                    w = max(1, int(cell_w * e))
                    new_x = base_x + (cell_w - w)
                    cell.place_configure(x=new_x, y=base_y,
                                         width=w, height=cell_h)
                elif direction == "top":
                    h = max(1, int(cell_h * e))
                    cell.place_configure(x=base_x, y=base_y,
                                         width=cell_w, height=h)
                elif direction == "bottom":
                    h = max(1, int(cell_h * e))
                    new_y = base_y + (cell_h - h)
                    cell.place_configure(x=base_x, y=new_y,
                                         width=cell_w, height=h)
            except Exception:
                return

            if s < steps:
                cell.after(max(1, duration_ms // steps),
                           lambda: grow(s + 1))

        return grow

    for i, cell in enumerate(cells):
        r = i // cols
        c = i % cols
        x = c * (cell_w + gap) + gap // 2
        y = r * (cell_h + gap) + gap // 2

        try:
            if direction in ("left", "right"):
                start_x = x if direction == "left" else x + cell_w - 1
                cell.place_configure(x=start_x, y=y,
                                     width=1, height=cell_h)
            else:
                start_y = y if direction == "top" else y + cell_h - 1
                cell.place_configure(x=x, y=start_y,
                                     width=cell_w, height=1)
        except Exception:
            continue

        stagger = i * 8
        grower = make_grower(i, x, y)
        cell.after(stagger, lambda g=grower: g(1))


def run_palette_effect(cells, cell_w, cell_h, cols):
    effect = get_effect()
    if effect == "cascade":
        animate_cells_cascade(cells, cell_w, cell_h, cols,
                              delay_ms=15, duration_ms=100, steps=5)
    else:
        animate_cells_book(cells, cell_w, cell_h, cols, effect,
                           duration_ms=220, steps=10)


class MetroButton(tk.Label):
    def __init__(self, parent, text, command,
                 primary=False, danger=False,
                 padx=20, pady=8,
                 font=None, width=None):
        self.primary = primary
        self.danger = danger

        if primary:
            base = get_accent()
            fg = accent_fg(base)
        elif danger:
            base = "#c42b1c"
            fg = "#ffffff"
        else:
            base = PANEL
            fg = TEXT

        self._base_bg = base
        self._fg = fg
        self._hover_bg = lighten(base, 0.12)
        self._press_bg = darken(base, 0.18)

        super().__init__(
            parent, text=text, bg=base, fg=fg,
            font=font or (UI_FONT, 9, "bold"),
            padx=padx, pady=pady,
            cursor="hand2", bd=0, highlightthickness=0, takefocus=0,
        )
        if width:
            self.configure(width=width)
        self._command = command

        self.bind("<Enter>", lambda e: self.config(bg=self._hover_bg))
        self.bind("<Leave>", lambda e: self.config(bg=self._base_bg))
        self.bind("<ButtonPress-1>", lambda e: self.config(bg=self._press_bg))
        self.bind("<ButtonRelease-1>", self._on_release)

    def _on_release(self, e):
        self.config(bg=self._hover_bg)
        if self._command:
            self._command()

    def set_accent(self, color=None):
        if not self.primary:
            return
        base = color if color else get_accent()
        fg = accent_fg(base)
        self._base_bg = base
        self._fg = fg
        self._hover_bg = lighten(base, 0.12)
        self._press_bg = darken(base, 0.18)
        self.config(bg=base, fg=fg)


class SquareIconButton(tk.Canvas):
    def __init__(self, parent, box, image_path, fallback_text, command):
        super().__init__(
            parent, width=box, height=box,
            bg=BG, highlightthickness=0, bd=0,
        )
        self.box = int(box)
        self.image_path = image_path
        self.fallback_text = fallback_text
        self.command = command
        self._photo = None
        self._photo_accent = None
        self._hovered = False
        self._pressed = False

        self._build_icon()
        self._draw()

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)

    def _build_icon(self):
        accent = get_accent()
        icon_color = "#ffffff"
        bw = SQUARE_BORDER_WIDTH
        ICON = int(self.box * 0.55 - bw * 2)
        if ICON < 6:
            ICON = 6
        self._photo = tint_icon(self.image_path, icon_color, ICON)
        self._photo_accent = accent
        self._icon_color = icon_color

    def _draw(self):
        self.delete("all")
        bw = SQUARE_BORDER_WIDTH
        pad = int(bw)

        accent = get_accent()
        fill_color = BG
        if self._pressed:
            fill_color = blend(BG, accent, 0.35)
        elif self._hovered:
            fill_color = blend(BG, accent, 0.18)

        self.create_rectangle(
            pad, pad, self.box - pad, self.box - pad,
            outline=accent, width=bw, fill=fill_color,
        )
        cx = self.box / 2
        cy = self.box / 2
        if self._photo is not None:
            self.create_image(cx, cy, image=self._photo)
        else:
            self.create_text(
                cx, cy, text=self.fallback_text,
                fill=accent_fg(accent), font=(UI_FONT, 12, "bold"),
            )

    def _on_enter(self, e):
        self._hovered = True
        self._draw()
        self.configure(cursor="hand2")

    def _on_leave(self, e):
        self._hovered = False
        self._pressed = False
        self._draw()

    def _on_press(self, e):
        self._pressed = True
        self._draw()

    def _on_release(self, e):
        self._pressed = False
        self._draw()
        if self.command:
            self.command()

    def set_accent(self, accent):
        self._build_icon()
        self._draw()


MetroIconButton = SquareIconButton


class OutlinedButton(tk.Frame):
    def __init__(self, parent, text, command,
                 padx=10, pady=8,
                 font=None):
        self.command = command
        self._selected = False
        self._text = text

        super().__init__(parent, bg=BG)
        self.label = tk.Label(
            self, text=text,
            bg=PANEL, fg=TEXT,
            font=font or (UI_FONT, 9, "bold"),
            padx=padx, pady=pady,
            cursor="hand2", bd=0,
            highlightthickness=1,
            highlightbackground="#ffffff",
            highlightcolor="#ffffff",
            anchor="w",
        )
        self.label.pack(fill="both", expand=True)

        self.label.bind("<Enter>", self._on_enter)
        self.label.bind("<Leave>", self._on_leave)
        self.label.bind("<ButtonPress-1>", self._on_press)
        self.label.bind("<ButtonRelease-1>", self._on_release)

    def _on_enter(self, e):
        if not self._selected:
            self.label.config(bg=lighten(PANEL, 0.10))

    def _on_leave(self, e):
        if not self._selected:
            self.label.config(bg=PANEL)

    def _on_press(self, e):
        if not self._selected:
            self.label.config(bg=darken(PANEL, 0.20))

    def _on_release(self, e):
        if not self._selected:
            self.label.config(bg=PANEL)
        if self.command:
            self.command()

    def set_selected(self, selected):
        self._selected = selected
        if selected:
            base = get_accent()
            self.label.config(bg=base, fg=accent_fg(base))
        else:
            self.label.config(bg=PANEL, fg=TEXT)

    def refresh_accent(self):
        if self._selected:
            base = get_accent()
            self.label.config(bg=base, fg=accent_fg(base))


TILE_COLORS = [
    "#ff6b6b", "#ff4040", "#c92a2a", "#8b1a1a",
    "#ffa94d", "#ff8800", "#cc6600", "#804000",
    "#ffd43b", "#ffcc00", "#b38f00", "#665200",
    "#a9e34b", "#80ff00", "#4d9900", "#2b5500",
    "#51cf66", "#30ff30", "#1a8a1a", "#0d440d",
    "#38d9a9", "#00cc88", "#008055", "#004d33",
    "#4dabf7", "#0080ff", "#0055aa", "#003366",
    "#748ffc", "#3030ff", "#1a1a99", "#0d0d4d",
    "#da77f2", "#b060ff", "#7033cc", "#3d1a66",
    "#f783ac", "#ff30ff", "#b300b3", "#660066",
    "#ced4da", "#7a7a7a", "#4d4d4d", "#1a1a1a",
]

ACCENT_COLORS = list(TILE_COLORS)


class PaletteWindow:
    WIN_W = 400
    WIN_H = 510

    def __init__(self, root, on_live_preview, on_apply_color):
        self.root = root
        self.win = None
        self.key = None
        self.on_live_preview = on_live_preview
        self.on_apply_color = on_apply_color

        self.original_color = None
        self.preview_color = None
        self.cells = []
        self.cells_by_color = {}
        self.swatch = None
        self.hex_lbl = None
        self.apply_btn = None
        self.grid_wrap = None

    def show(self, key, current_hex, current_no_bg=None):
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
        self.cells = []
        self.cells_by_color = {}

        self.win = tk.Toplevel(self.root)
        self.win.title(f"Color: {key}")
        self.win.configure(bg=BG)
        self.win.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.win.resizable(False, False)

        tk.Frame(self.win, bg=get_accent(), height=3).pack(
            side="top", fill="x"
        )

        btns = tk.Frame(self.win, bg=BG)
        btns.pack(side="bottom", fill="x", padx=12, pady=(8, 12))

        MetroButton(btns, "CANCEL", self._cancel,
                    primary=False, padx=18, pady=8
                    ).pack(side="right", padx=(6, 0))

        self.apply_btn = MetroButton(btns, "APPLY", self._apply,
                                     primary=True, padx=18, pady=8)
        self.apply_btn.pack(side="right")

        top = tk.Frame(self.win, bg=BG)
        top.pack(side="top", fill="x", padx=12, pady=(10, 0))

        tk.Label(top, text=f"COLOR · {key}",
                 bg=BG, fg=TEXT,
                 font=(UI_FONT, 10, "bold"),
                 anchor="w").pack(fill="x")

        swatch_row = tk.Frame(top, bg=BG)
        swatch_row.pack(fill="x", pady=(6, 0))

        self.swatch = tk.Label(swatch_row, bg=current_hex, text="", height=2)
        self.swatch.pack(side="left", fill="x", expand=True)

        self.hex_lbl = tk.Label(swatch_row, text=current_hex,
                                bg=BG, fg=TEXT_MUTED,
                                font=(UI_FONT, 9, "bold"),
                                anchor="e", width=9)
        self.hex_lbl.pack(side="right", padx=(8, 0))

        cols = 8
        rows = (len(TILE_COLORS) + cols - 1) // cols
        pad_outer = 6
        gap = 2

        avail_w = self.WIN_W - pad_outer * 2
        avail_h = self.WIN_H - 90 - 55 - 3

        cell_w = (avail_w - (cols - 1) * gap) // cols
        cell_h = (avail_h - (rows - 1) * gap) // rows

        self.grid_wrap = tk.Frame(self.win, bg=BG)
        self.grid_wrap.pack(side="top", fill="both", expand=True,
                            padx=pad_outer, pady=(10, 0))
        self.grid_wrap.pack_propagate(False)

        for i, c in enumerate(TILE_COLORS):
            r = i // cols
            col = i % cols
            x = col * (cell_w + gap) + gap // 2
            y = r * (cell_h + gap) + gap // 2

            cell = tk.Label(
                self.grid_wrap, bg=c, text="",
                bd=0, highlightthickness=0, cursor="hand2",
            )
            cell.place(x=x, y=y, width=cell_w, height=cell_h)
            cell.bind("<Button-1>", lambda e, cc=c: self._pick(cc))

            self.cells.append(cell)
            self.cells_by_color[c] = cell

        self._highlight(current_hex)
        self.apply_btn.set_accent(current_hex)

        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        for cell in self.cells:
            try:
                cell.place_configure(width=1, height=1)
            except Exception:
                pass
        try:
            self.win.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.win.after(20, lambda: run_palette_effect(
            self.cells, cell_w, cell_h, cols))

    def _pick(self, color):
        self.preview_color = color
        self.swatch.config(bg=color)
        self.hex_lbl.config(text=color)
        if self.apply_btn is not None:
            try:
                self.apply_btn.set_accent(color)
            except Exception:
                pass
        self._highlight(color)
        if self.on_live_preview:
            self.on_live_preview(self.key, color)

    def _highlight(self, color):
        for c, cell in self.cells_by_color.items():
            try:
                cell.config(highlightthickness=0)
            except Exception:
                pass
        if color in self.cells_by_color:
            try:
                self.cells_by_color[color].config(
                    highlightthickness=2,
                    highlightbackground="#ffffff",
                    highlightcolor="#ffffff",
                )
            except Exception:
                pass

    def _apply(self):
        if self.preview_color:
            self.on_apply_color(self.key, self.preview_color)
        self._close()

    def _cancel(self):
        if self.key:
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
        self.cells = []
        self.cells_by_color = {}
        self.apply_btn = None


class AccentPalette:
    WIN_W = 400
    WIN_H = 510

    def __init__(self, root, on_preview, on_apply):
        self.root = root
        self.win = None
        self.on_preview = on_preview
        self.on_apply = on_apply
        self.original = None
        self.selected = None
        self.cells = []
        self.cells_by_color = {}
        self.selected_lbl = None
        self.apply_btn = None
        self.grid_wrap = None

    def show(self, current_hex):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return

        self.original = current_hex
        self.selected = current_hex
        self.cells = []
        self.cells_by_color = {}

        self.win = tk.Toplevel(self.root)
        self.win.title("Accent color")
        self.win.configure(bg=BG)
        self.win.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.win.resizable(False, False)

        tk.Frame(self.win, bg=get_accent(), height=3).pack(
            side="top", fill="x"
        )

        btns = tk.Frame(self.win, bg=BG)
        btns.pack(side="bottom", fill="x", padx=12, pady=(8, 12))

        MetroButton(btns, "CANCEL", self._cancel,
                    primary=False, padx=18, pady=8
                    ).pack(side="right", padx=(6, 0))

        self.apply_btn = MetroButton(btns, "APPLY", self._apply,
                                     primary=True, padx=18, pady=8)
        self.apply_btn.pack(side="right")

        top = tk.Frame(self.win, bg=BG)
        top.pack(side="top", fill="x", padx=12, pady=(10, 0))

        tk.Label(top, text="ACCENT COLOR",
                 bg=BG, fg=TEXT,
                 font=(UI_FONT, 10, "bold"),
                 anchor="w").pack(fill="x")

        self.selected_lbl = tk.Label(top, text=current_hex,
                                     bg=BG, fg=TEXT_MUTED,
                                     font=(UI_FONT, 9, "bold"),
                                     anchor="e")
        self.selected_lbl.pack(fill="x", pady=(4, 0))

        cols = 8
        rows = (len(ACCENT_COLORS) + cols - 1) // cols
        pad_outer = 6
        gap = 2

        avail_w = self.WIN_W - pad_outer * 2
        avail_h = self.WIN_H - 90 - 55 - 3

        cell_w = (avail_w - (cols - 1) * gap) // cols
        cell_h = (avail_h - (rows - 1) * gap) // rows

        self.grid_wrap = tk.Frame(self.win, bg=BG)
        self.grid_wrap.pack(side="top", fill="both", expand=True,
                            padx=pad_outer, pady=(10, 0))
        self.grid_wrap.pack_propagate(False)

        for i, c in enumerate(ACCENT_COLORS):
            r = i // cols
            col = i % cols
            x = col * (cell_w + gap) + gap // 2
            y = r * (cell_h + gap) + gap // 2

            cell = tk.Label(
                self.grid_wrap, bg=c, text="",
                bd=0, highlightthickness=0, cursor="hand2",
            )
            cell.place(x=x, y=y, width=cell_w, height=cell_h)
            cell.bind("<Button-1>", lambda e, cc=c: self._pick(cc))

            self.cells.append(cell)
            self.cells_by_color[c] = cell

        self._highlight(self.original)
        self.apply_btn.set_accent(current_hex)

        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        for cell in self.cells:
            try:
                cell.place_configure(width=1, height=1)
            except Exception:
                pass
        try:
            self.win.attributes("-alpha", 1.0)
        except Exception:
            pass
        self.win.after(20, lambda: run_palette_effect(
            self.cells, cell_w, cell_h, cols))

    def _pick(self, color):
        self.selected = color
        if self.selected_lbl is not None:
            try:
                self.selected_lbl.config(text=color)
            except Exception:
                pass
        if self.apply_btn is not None:
            try:
                self.apply_btn.set_accent(color)
            except Exception:
                pass
        self._highlight(color)
        if self.on_preview:
            self.on_preview(color)

    def _highlight(self, color):
        for c, cell in self.cells_by_color.items():
            try:
                cell.config(highlightthickness=0)
            except Exception:
                pass
        if color in self.cells_by_color:
            try:
                self.cells_by_color[color].config(
                    highlightthickness=2,
                    highlightbackground="#ffffff",
                    highlightcolor="#ffffff",
                )
            except Exception:
                pass

    def _apply(self):
        if self.selected:
            self.on_apply(self.selected)
        self._close()

    def _cancel(self):
        if self.on_preview and self.original:
            self.on_preview(self.original)
        self._close()

    def _close(self):
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:
                pass
            self.win = None
        self.cells = []
        self.cells_by_color = {}
        self.selected_lbl = None
        self.apply_btn = None


class DiskDetailWindow:
    WIN_W = 280
    WIN_H = 160

    def __init__(self, root):
        self.root = root
        self.win = None
        self.canvas = None
        self.mountpoint = None
        self._photo = None

    def show(self, mountpoint):
        self.mountpoint = mountpoint
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self._refresh()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("Disk")
        self.win.configure(bg=BG)
        self.win.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.win.resizable(False, False)

        self.canvas = tk.Canvas(self.win, bg=BG,
                                highlightthickness=0, bd=0,
                                width=self.WIN_W,
                                height=self.WIN_H)
        self.canvas.pack(fill="both", expand=True)

        self.win.protocol("WM_DELETE_WINDOW", self._on_close)
        fade_in(self.win)
        self._refresh()

    def _refresh(self):
        try:
            usage = psutil.disk_usage(self.mountpoint)
        except Exception:
            return

        percent = usage.percent
        used = usage.used
        total = usage.total
        free = usage.free

        cw = self.WIN_W
        ch = self.WIN_H

        accent = get_accent() or DEFAULT_ACCENT
        try:
            _ = hex_to_rgb(accent)
        except Exception:
            accent = DEFAULT_ACCENT
        accent_dark = darken(accent, 0.45)

        img = Image.new("RGB", (cw, ch), accent_dark)
        d = ImageDraw.Draw(img)

        filled_h = int(ch * percent / 100)
        empty_h = ch - filled_h

        if empty_h > 0:
            d.rectangle((0, 0, cw, empty_h), fill=accent)
        if filled_h > 0:
            d.rectangle((0, empty_h, cw, ch), fill=accent_dark)

        fg_light = (0, 0, 0) if lum(hex_to_rgb(accent)) > 140 else (255, 255, 255)
        fg_dark = (0, 0, 0) if lum(hex_to_rgb(accent_dark)) > 140 else (255, 255, 255)

        f_title = get_font(13)
        title = "DISK USAGE"
        bb = d.textbbox((0, 0), title, font=f_title)
        d.text((16 - bb[0], 16 - bb[1]), title, font=f_title, fill=fg_light)

        info_text = (f"{human_bytes(used)} / {human_bytes(total)}"
                     f"   ·   free {human_bytes(free)}")
        f_info = get_font(11)
        bb2 = d.textbbox((0, 0), info_text, font=f_info)
        d.text((16 - bb2[0], 40 - bb2[1]),
               info_text, font=f_info, fill=fg_light)

        pct_text = f"{percent:.0f}%"
        f_pct = get_font(64)
        bb3 = d.textbbox((0, 0), pct_text, font=f_pct)
        th3 = bb3[3] - bb3[1]
        tx3 = 16 - bb3[0]
        ty3 = ch - 16 - th3 - bb3[1]
        d.text((tx3, ty3), pct_text, font=f_pct, fill=fg_dark)

        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    def _on_close(self):
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.canvas = None
        self._photo = None


class HelpWindow:
    WIN_W = 520
    WIN_H = 560

    def __init__(self, root):
        self.root = root
        self.win = None
        self.canvas = None
        self.scroll_frame = None
        self.effect_btns = {}
        self._strip_widgets = []
        self._built = False

    def show(self):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self._refresh_effect_buttons()
            return

        self.win = tk.Toplevel(self.root)
        self.win.title("Help")
        self.win.configure(bg=BG)
        self.win.geometry(f"{self.WIN_W}x{self.WIN_H}")
        self.win.resizable(False, False)
        self.win.withdraw()

        self._top_strip = tk.Frame(self.win, bg=get_accent(), height=3)
        self._top_strip.pack(side="top", fill="x")

        container = tk.Frame(self.win, bg=BG)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        sb = tk.Scrollbar(container, orient="vertical",
                          command=self.canvas.yview,
                          bg=PANEL, troughcolor=BG, bd=0,
                          highlightthickness=0, width=8)
        sb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=sb.set)

        self.scroll_frame = tk.Frame(self.canvas, bg=BG)
        self._canvas_window = self.canvas.create_window(
            (0, 0), window=self.scroll_frame, anchor="nw"
        )

        self.scroll_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            )
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(
                self._canvas_window, width=e.width
            )
        )

        self.win.bind("<Enter>", self._bind_wheel)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

        self.win.after(1, self._build_content_deferred)

    def _build_content_deferred(self):
        if self.win is None or not self.win.winfo_exists():
            return
        try:
            self._build_content()
            self._built = True
        except Exception as e:
            print("help build:", e)

    def _bind_wheel(self, e=None):
        self.win.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.win.bind_all("<Button-4>", self._on_mousewheel_linux_up, add="+")
        self.win.bind_all("<Button-5>", self._on_mousewheel_linux_down, add="+")

    def _on_mousewheel(self, event):
        if self.win is None or not self.win.winfo_exists():
            return
        try:
            delta = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(delta, "units")
        except Exception:
            pass

    def _on_mousewheel_linux_up(self, event):
        try:
            self.canvas.yview_scroll(-1, "units")
        except Exception:
            pass

    def _on_mousewheel_linux_down(self, event):
        try:
            self.canvas.yview_scroll(1, "units")
        except Exception:
            pass

    def _section_title(self, parent, text):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=20, pady=(16, 6))

        inner = tk.Frame(wrap, bg=BG)
        inner.pack(anchor="w")

        lbl = tk.Label(
            inner, text=text.upper(),
            bg=BG, fg=TEXT,
            font=(UI_FONT, 11, "bold"),
            anchor="w",
        )
        lbl.pack(anchor="w")

        strip = tk.Frame(inner, bg=get_accent(), height=2, width=1)
        strip.pack(anchor="w", pady=(6, 0))

        self._strip_widgets.append((strip, lbl))

    def _row(self, parent, left, right):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill="x", padx=20, pady=2)

        tk.Label(
            row, text=left,
            bg=BG, fg=TEXT,
            font=(UI_FONT, 9, "bold"),
            anchor="w", width=22,
        ).pack(side="left")

        tk.Label(
            row, text=right,
            bg=BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9),
            anchor="w", justify="left",
        ).pack(side="left", fill="x", expand=True)

    def _paragraph(self, parent, text):
        tk.Label(
            parent, text=text,
            bg=BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9),
            anchor="w", justify="left",
            wraplength=self.WIN_W - 60,
        ).pack(fill="x", padx=20, pady=(2, 0))

    def _build_content(self):
        header = tk.Frame(self.scroll_frame, bg=BG)
        header.pack(fill="x", padx=20, pady=(20, 4))

        hdr_lbl = tk.Label(
            header, text=APP_NAME,
            bg=BG, fg=TEXT,
            font=(UI_FONT, 18, "bold"),
            anchor="w",
        )
        hdr_lbl.pack(anchor="w")

        tk.Label(
            header, text=f"System resource monitor  ·  v{APP_VERSION}",
            bg=BG, fg=TEXT_MUTED,
            font=(UI_FONT, 9),
            anchor="w",
        ).pack(fill="x", pady=(2, 0))

        hdr_strip = tk.Frame(header, bg=get_accent(), height=2, width=1)
        hdr_strip.pack(anchor="w", pady=(10, 0))
        self._strip_widgets.append((hdr_strip, hdr_lbl))

        self._section_title(self.scroll_frame, "Tiles")
        self._row(self.scroll_frame, "Left click", "set active metric in tray")
        self._row(self.scroll_frame, "Right click", "change tile color")
        self._row(self.scroll_frame, "Mouse wheel", "change font size")

        self._section_title(self.scroll_frame, "Special tiles")
        self._row(self.scroll_frame, "TEMP (left click)",
                  "switch between °C and °F")
        self._row(self.scroll_frame, "CUSTOM (left click)",
                  "type custom text (up to 3 chars)")
        self._row(self.scroll_frame, "DISK (double click)",
                  "open detailed disk usage")

        self._section_title(self.scroll_frame, "Bottom bar")
        self._row(self.scroll_frame, "?  (help)", "open this window")
        self._row(self.scroll_frame, "■  (accent)",
                  "change global accent color")
        self._row(self.scroll_frame, "✕  (clear)",
                  "reset all colors to default")

        self._section_title(self.scroll_frame, "System tray")
        self._row(self.scroll_frame, "Left click", "open main window")
        self._row(self.scroll_frame, "Right click",
                  "menu with EXIT option")

        self._section_title(self.scroll_frame, "Animation")
        self._paragraph(
            self.scroll_frame,
            "Choose the animation played when opening a color palette "
            "(right-click a tile)."
        )

        effects = [
            ("cascade", "CASCADE"),
            ("left",    "BOOK · LEFT"),
            ("right",   "BOOK · RIGHT"),
            ("top",     "BOOK · TOP"),
            ("bottom",  "BOOK · BOTTOM"),
        ]
        for e, label in effects:
            btn = OutlinedButton(
                self.scroll_frame, label,
                command=lambda k=e: self._set_effect(k),
                padx=12, pady=6,
            )
            btn.pack(fill="x", padx=20, pady=2)
            self.effect_btns[e] = btn

        self._refresh_effect_buttons()

        self._section_title(self.scroll_frame, "About")
        self._paragraph(self.scroll_frame, "Author: M1han")
        self._paragraph(self.scroll_frame, "github.com/MihanPROJECTS/OS-RSM")

        tk.Frame(self.scroll_frame, bg=BG, height=20).pack(fill="x")

        self.win.after(30, self._animate_strips)

    def _animate_strips(self):
        if self.win is None or not self.win.winfo_exists():
            return
        for strip, lbl in self._strip_widgets:
            try:
                target_w = lbl.winfo_reqwidth()
                if target_w < 1:
                    target_w = 60
                self._animate_strip(strip, target_w)
            except Exception:
                pass

    def _animate_strip(self, strip, target_w):
        steps = 6

        def step(i):
            if self.win is None or not self.win.winfo_exists():
                return
            try:
                if not strip.winfo_exists():
                    return
            except Exception:
                return
            t = i / steps
            e = 1 - (1 - t) ** 2
            w = max(1, int(target_w * e))
            try:
                strip.config(width=w)
            except Exception:
                return
            if i < steps:
                strip.after(30, lambda: step(i + 1))

        step(1)

    def _set_effect(self, e):
        save_effect(e)
        self._refresh_effect_buttons()

    def _refresh_effect_buttons(self):
        cur = get_effect()
        for e, btn in self.effect_btns.items():
            if btn is None:
                continue
            btn.set_selected(cur == e)

    def _on_close(self):
        try:
            self.win.unbind_all("<MouseWheel>")
            self.win.unbind_all("<Button-4>")
            self.win.unbind_all("<Button-5>")
        except Exception:
            pass
        if self.win is not None:
            self.win.destroy()
            self.win = None
        self.canvas = None
        self.scroll_frame = None
        self.effect_btns = {}
        self._strip_widgets = []
        self._built = False


class Tile:
    SIZE = TILE_SIZE

    def __init__(self, parent, key, get_color_fn, on_click, on_double,
                 on_edit, get_display, is_active, get_grid_pos):
        self.parent = parent
        self.key = key
        self.get_color_fn = get_color_fn
        self.on_click = on_click
        self.on_double = on_double
        self.on_edit = on_edit
        self.get_display = get_display
        self.is_active = is_active
        self.get_grid_pos = get_grid_pos

        self.canvas = tk.Canvas(
            parent, width=self.SIZE, height=self.SIZE,
            bg=BG, highlightthickness=0, bd=0
        )
        self.hovered = False
        self._photo = None
        self._last_render_key = None
        self._pulse_until = 0.0

        self._draw(force=True)

        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right_click)

    def _render_key(self):
        value_text, label = self.get_display()
        color = self.get_color_fn(self.key)
        pulse = time.time() < self._pulse_until
        return (value_text, label, color,
                self.hovered, self.is_active(), pulse)

    def _draw(self, force=False):
        key = self._render_key()
        if not force and key == self._last_render_key:
            return
        self._last_render_key = key

        value_text, label, color = key[0], key[1], key[2]
        pulse = key[5]

        active = self.is_active() or pulse

        img = render_tile_image(
            color, value_text, label,
            self.hovered, active,
            state.font_scale,
        )
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)

    def refresh(self):
        self._draw()

    def _on_enter(self, e):
        self.hovered = True
        self._draw()
        self.canvas.configure(cursor="hand2")

    def _on_leave(self, e):
        self.hovered = False
        self._draw()

    def _on_press(self, e):
        self._pulse_until = time.time() + 0.12
        self._draw(force=True)
        self.canvas.after(130, self._pulse_end)

    def _pulse_end(self):
        self._pulse_until = 0.0
        self._draw(force=True)

    def _on_release(self, e):
        if self.on_click:
            self.on_click()

    def _on_double(self, e):
        if self.on_double:
            self.on_double()

    def _on_right_click(self, e):
        if self.on_edit:
            self.on_edit(self.key)


class ConfigWindow:
    GAP = 8

    def __init__(self, root):
        self.root = root
        root.title(APP_NAME + " v" + APP_VERSION)

        TILE = TILE_SIZE
        GAP = self.GAP
        PAD = 16
        VER_H = 34

        w = 3 * TILE + 2 * GAP + 2 * PAD + 4
        h = 3 * TILE + 2 * GAP + PAD + VER_H
        root.geometry(f"{w}x{h}")
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        self.tiles = {}
        self.custom_editing = False
        self._wheel_pending = False
        self._wheel_dir = 0
        self._grid_positions = {}
        self._grid_cols = 3
        self._grid_rows = 1

        self.disk_detail = DiskDetailWindow(root)
        self.help_window = HelpWindow(root)

        self.palette = PaletteWindow(
            root,
            on_live_preview=self._live_preview,
            on_apply_color=self._apply_color,
        )
        self.accent_palette = AccentPalette(
            root,
            on_preview=self._preview_accent,
            on_apply=self._apply_accent,
        )

        self.content = tk.Frame(root, bg=BG)
        self.content.place(x=0, y=0, relwidth=1, relheight=1)

        self.grid_frame = tk.Frame(self.content, bg=BG)
        self.grid_frame.pack(padx=PAD, pady=(PAD, PAD + VER_H))

        bottom = tk.Frame(self.content, bg=BG)
        bottom.place(relx=0.0, rely=1.0, anchor="sw", x=PAD, y=-6)

        BOX = 34
        self.help_btn = MetroIconButton(bottom, BOX,
                                        VOPROS_ICON_PATH, "?",
                                        self.help_window.show)
        self.help_btn.pack(side="left")

        self.pall_btn = MetroIconButton(bottom, BOX,
                                        PALL_ICON_PATH, "■",
                                        self._open_accent)
        self.pall_btn.pack(side="left", padx=(6, 0))

        self.clear_btn = MetroIconButton(bottom, BOX,
                                         CLEAR_ICON_PATH, "✕",
                                         self._clear_colors)
        self.clear_btn.pack(side="left", padx=(6, 0))

        tk.Label(self.content, text="v" + APP_VERSION,
                 bg=BG, fg=TEXT_MUTED, font=(UI_FONT, 7)
                 ).place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)

        root.bind("<MouseWheel>", self._on_wheel)
        root.bind("<Button-4>", lambda e: self._schedule_wheel(1))
        root.bind("<Button-5>", lambda e: self._schedule_wheel(-1))

        self.build_ui()
        self._bind_global_click()
        self.refresh_loop()

    def _open_accent(self):
        self.accent_palette.show(get_accent())

    def _preview_accent(self, color):
        for k, tile in self.tiles.items():
            CONFIG.setdefault("colors", {})[k] = color
            tile._draw(force=True)
        for btn in (self.help_btn, self.pall_btn, self.clear_btn):
            try:
                btn.set_accent(color)
            except Exception:
                pass

    def _apply_accent(self, color):
        save_accent(color)
        for k in list(CONFIG.get("tile_order", [])):
            save_color(k, color)
        save_config(CONFIG)
        self._redraw_all()
        for btn in (self.help_btn, self.pall_btn, self.clear_btn):
            try:
                btn.set_accent(color)
            except Exception:
                pass

    def _live_preview(self, key, color):
        CONFIG.setdefault("colors", {})[key] = color
        save_config(CONFIG)
        tile = self.tiles.get(key)
        if tile:
            tile._draw(force=True)

    def _apply_color(self, key, color):
        save_color(key, color)
        tile = self.tiles.get(key)
        if tile:
            tile._draw(force=True)

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
        s = state.font_scale + direction * 0.1
        s = max(0.7, min(1.5, round(s, 2)))
        if s == state.font_scale:
            return
        state.font_scale = s
        CONFIG["font_scale"] = s
        save_config(CONFIG)
        self._redraw_all()

    def _clear_colors(self):
        reset_colors()
        accent = get_accent()
        for btn in (self.help_btn, self.pall_btn, self.clear_btn):
            try:
                btn.set_accent(accent)
            except Exception:
                pass
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

    def _visible_keys(self):
        order = CONFIG.get("tile_order", list(ALL_KEYS) + [CUSTOM_KEY])
        hidden = set(CONFIG.get("hidden_tiles", []))
        visible = [k for k in order if k not in hidden]
        if CUSTOM_KEY in visible:
            visible.remove(CUSTOM_KEY)
        visible.append(CUSTOM_KEY)
        return visible

    def _grid_pos_for(self, key):
        pos = self._grid_positions.get(key)
        if pos is None:
            return (0, 0, self._grid_cols, self._grid_rows)
        r, c = pos
        return (r, c, self._grid_cols, self._grid_rows)

    def build_ui(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles.clear()
        self._grid_positions.clear()

        keys = self._visible_keys()
        cols = 3
        rows = max(1, (len(keys) + cols - 1) // cols)
        self._grid_cols = cols
        self._grid_rows = rows

        for idx, k in enumerate(keys):
            r = idx // 3
            c = idx % 3
            self._grid_positions[k] = (r, c)

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
                is_active=lambda kk=k: state.current == kk,
                get_grid_pos=lambda kk=k: self._grid_pos_for(kk),
            )
            tile.canvas.grid(row=r, column=c,
                             padx=self.GAP // 2, pady=self.GAP // 2)
            self.tiles[k] = tile

    def _open_palette(self, key):
        self.palette.show(key, get_color(key))

    def _display_for(self, key):
        def fn():
            with state.lock:
                custom = state.custom_text
                raw = state.values.get(key, 0)
                unit = state.temp_unit

            if key == CUSTOM_KEY:
                return (custom, "CUSTOM") if custom else ("...", "CUSTOM")

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
            state.current = CUSTOM_KEY
            CONFIG["current"] = CUSTOM_KEY
            save_config(CONFIG)
            self._redraw_all()
            return

        if key == "TEMP":
            state.current = "TEMP"
            CONFIG["current"] = "TEMP"
            state.temp_unit = "F" if state.temp_unit == "C" else "C"
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
        self.root.after(1000, self.refresh_loop)


def ui_pump(root, icon):
    try:
        while True:
            cmd = ui_queue.get_nowait()
            if cmd == "open_window":
                root.deiconify()
                root.lift()
                root.focus_force()
                fade_in(root, duration_ms=120, steps=6)
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
    print("[pall icon path]:", PALL_ICON_PATH)
    print("[pall icon exists]:", os.path.exists(PALL_ICON_PATH))
    print("[assets dir]:", ASSETS_DIR)

    threading.Thread(target=stats_worker, daemon=True).start()

    icon = build_tray()

    def run_tray():
        try:
            icon.run()
        except Exception as e:
            import traceback
            print("[tray] ERROR:", e)
            traceback.print_exc()

    threading.Thread(target=run_tray, daemon=True).start()

    root = tk.Tk()
    ConfigWindow(root)
    root.withdraw()

    ui_pump(root, icon)
    root.mainloop()


if __name__ == "__main__":
    main()
