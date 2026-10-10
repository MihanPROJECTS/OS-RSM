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
APP_VERSION = "1.0b"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
CONFIG_PATH = os.path.expanduser("~/.config/tray-monitor.json")


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
    "CPU":  {"fn": get_cpu,    "color": "#59ff00"},
    "RAM":  {"fn": get_ram,    "color": "#c300eb"},
    "DISK": {"fn": get_disk,   "color": "#ffc800"},
    "SWAP": {"fn": get_swap,   "color": "#0080ff"},
    "TEMP": {"fn": get_temp_c, "color": "#ff4040"},
}

CUSTOM_KEY = "CUSTOM"
CUSTOM_COLOR = "#7a7a7a"


def hex_to_rgb(h):
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def lum(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


_font_cache = {}


def get_font(size):
    if size in _font_cache:
        return _font_cache[size]

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
    """Применить множитель шрифта, не ниже 6."""
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

    if raw is None:
        val = None
    else:
        val = int(round(raw))

    special_temp = False

    if name == CUSTOM_KEY:
        color = hex_to_rgb(CUSTOM_COLOR)
        text = custom if custom else "···"
        if len(text) <= 2:
            f_big = get_font(_scaled(40))
        else:
            f_big = get_font(_scaled(28))
    elif name == "TEMP":
        color = hex_to_rgb(METRICS["TEMP"]["color"])
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
        color = hex_to_rgb(METRICS[name]["color"])
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
        deg = "°C" if unit == "C" else "°F"
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
        title=APP_NAME + " · v" + APP_VERSION,
        menu=pystray.Menu(
            item("Открыть", on_open, default=True),
            item("Выход", on_quit),
        ),
    )

    def refresh():
        while True:
            try:
                icon.icon = render_tray_icon()
            except Exception as e:
                print("tray refresh:", e)
            time.sleep(1.0)

    t = threading.Thread(target=refresh, daemon=True)
    t.start()
    return icon


BG = "#1e1e1e"
TEXT_MUTED = "#777777"

TILE_SIZE = 96
TILE_RENDER_SIZE = 192

TILE_FONT_VALUE = 72
TILE_FONT_LABEL = 22


def render_tile_image(color_hex, value_text, label_text,
                      hovered, pressed, active):
    s = TILE_RENDER_SIZE
    img = Image.new("RGB", (s, s), color_hex)
    d = ImageDraw.Draw(img)

    rgb = hex_to_rgb(color_hex)
    if lum(rgb) > 140:
        fg = (0, 0, 0)
    else:
        fg = (255, 255, 255)

    if active:  # Полоска вверху
        d.rectangle((0, 0, s, 8), fill=(255, 255, 255))
        # d.rectangle((0, s - 8, s, s), fill=(255, 255, 255))
        # d.rectangle((0, 0, 8, s), fill=(255, 255, 255))
        # d.rectangle((s - 8, 0, s, s), fill=(255, 255, 255))

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

    img = img.resize((TILE_SIZE, TILE_SIZE), Image.LANCZOS)

    if hovered or pressed:
        step = 5 if not pressed else 3
        dot = (0, 0, 0) if lum(rgb) > 140 else (255, 255, 255)
        alpha = 320 if not pressed else 700
        overlay = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for y in range(0, TILE_SIZE, step):
            for x in range(0, TILE_SIZE, step):
                od.point((x, y), fill=dot + (alpha,))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    return img


class Tile:
    SIZE = TILE_SIZE

    def __init__(self, parent, key, color, on_click, get_display, is_active):
        self.parent = parent
        self.key = key
        self.color = color
        self.on_click = on_click
        self.get_display = get_display
        self.is_active = is_active

        self.canvas = tk.Canvas(
            parent, width=self.SIZE, height=self.SIZE,
            bg=BG, highlightthickness=0, bd=0
        )
        self.hovered = False
        self.pressed = False
        self._photo = None
        self._draw()

        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

    def _draw(self):
        value_text, label = self.get_display()
        img = render_tile_image(
            self.color, value_text, label,
            self.hovered, self.pressed, self.is_active()
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
        self.pressed = False
        self._draw()

    def _on_press(self, e):
        self.pressed = True
        self._draw()

    def _on_release(self, e):
        was = self.pressed
        self.pressed = False
        self._draw()
        if was and self.on_click:
            self.on_click()


class ConfigWindow:
    COLS = 3
    GAP = 8

    def __init__(self, root):
        self.root = root
        root.title(APP_NAME + " · v" + APP_VERSION)

        TILE = TILE_SIZE
        GAP = self.GAP
        PAD = 16
        VER_H = 24

        w = 3 * TILE + 2 * GAP + 2 * PAD
        h = 3 * TILE + 2 * GAP + PAD + VER_H
        root.geometry(str(w) + "x" + str(h))
        root.configure(bg=BG)
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", root.withdraw)

        self.tiles = {}
        self.custom_editing = False

        self.grid_frame = tk.Frame(root, bg=BG)
        self.grid_frame.pack(padx=PAD, pady=(PAD, PAD + VER_H))

        # нижняя панель: версия + кнопки размера шрифта
        bottom = tk.Frame(root, bg=BG)
        bottom.place(relx=1.0, rely=1.0, anchor="se", x=-10, y=-6)

        tk.Label(
            bottom, text="  v" + APP_VERSION,
            bg=BG, fg=TEXT_MUTED, font=("Sans", 12)
        ).pack(side="right", padx=(0, 6))

        def make_btn(txt, delta):
            def on_click(_e=None):
                s = state.font_scale + delta
                s = max(0.7, min(1.5, round(s, 2)))
                state.font_scale = s
                CONFIG["font_scale"] = s
                save_config(CONFIG)
                self._redraw_all()
            b = tk.Label(
                bottom, text=txt, bg=BG, fg=TEXT_MUTED,
                font=("Sans", 10, "bold"), cursor="hand2", padx=3
            )
            b.pack(side="left")
            b.bind("<Button-1>", on_click)
            return b

        make_btn("−", -0.1)
        make_btn("+", +0.1)

        self.build_ui()
        self._bind_global_click()
        self.refresh_loop()

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
            tile._draw()

    def _finish_custom(self):
        if not self.custom_editing:
            return
        self.custom_editing = False
        CONFIG["custom_text"] = state.custom_text
        CONFIG["current"] = CUSTOM_KEY
        save_config(CONFIG)
        state.current = CUSTOM_KEY
        self._redraw_all()

    def build_ui(self):
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self.tiles.clear()

        keys = list(METRICS.keys()) + [CUSTOM_KEY]

        for idx, k in enumerate(keys):
            r = idx // 3
            c = idx % 3

            if k == CUSTOM_KEY:
                color = CUSTOM_COLOR
            else:
                color = METRICS[k]["color"]

            tile = Tile(
                self.grid_frame, k, color,
                on_click=(lambda kk=k: self.pick(kk)),
                get_display=self._display_for(k),
                is_active=(lambda kk=k: state.current == kk)
            )
            tile.canvas.grid(row=r, column=c,
                             padx=self.GAP // 2, pady=self.GAP // 2)
            self.tiles[k] = tile

    def _display_for(self, key):
        def fn():
            with state.lock:
                custom = state.custom_text
                raw = state.values.get(key, 0)
                unit = state.temp_unit

            if key == CUSTOM_KEY:
                if custom:
                    return custom, "CUSTOM"
                return "···", "CUSTOM"

            if key == "TEMP":
                if raw is None:
                    return "--", "TEMP"
                t = int(round(raw))
                if unit == "F":
                    t = int(round(t * 9 / 5 + 32))
                    return str(t), "TEMP°F"
                return str(t), "TEMP°C"

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
            tile._draw()

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
