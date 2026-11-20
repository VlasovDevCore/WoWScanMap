"""Passive Windows minimap alert. Run with Python and Pillow."""

import ctypes
import os
from pathlib import Path
import sys
from time import localtime, strftime
import tkinter as tk
from tkinter import messagebox
import winsound

from PIL import Image, ImageDraw, ImageGrab, ImageTk
from translations import LANGUAGES, MESSAGES


BACKGROUND = "#101613"
PANEL = "#19221D"
TEXT = "#F2F5EE"
MUTED = "#BAC9BE"
ACCENT = "#F1CB73"
INK = "#211805"
FOCUS = "#D6F0DD"
FONT = "Segoe UI"
APP_NAME = "WoW - ScanMap"
VERSION = "1.0.1"
APP_FILE = Path(sys.executable if getattr(sys, "frozen", False) else __file__)
DETECTION_FILE = APP_FILE.with_name("last_detection.png")
LANGUAGE_FILE = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WoWScanMap" / "language.txt"


def default_language():
    try:
        saved = LANGUAGE_FILE.read_text(encoding="utf-8").strip()
        if saved in LANGUAGES:
            return saved
    except OSError:
        pass
    language_id = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
    return {0x19: "ru", 0x04: "zh"}.get(language_id, "en")


def game_is_active():
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(ctypes.c_void_p(user32.GetForegroundWindow()), title, len(title))
    return any(name in title.value.casefold() for name in ("world of warcraft", "魔兽世界", "魔獸世界", "мир варкрафта"))


def yellow_spots(image, shape="circle"):
    """Return bounding boxes of compact yellow components inside the minimap."""
    image = image.convert("RGB")
    width, height = image.size
    pixels = image.load()
    dx_squared = [((2 * x + 1) / width - 1) ** 2 for x in range(width)]
    dy_squared = [((2 * y + 1) / height - 1) ** 2 for y in range(height)]
    circle = shape == "circle"
    yellow = set()
    for y in range(height):
        for x in range(width):
            if circle and dx_squared[x] + dy_squared[y] > 1:
                continue
            red, green, blue = pixels[x, y]
            if red >= 185 and green >= 130 and red >= green * 0.95 and green >= red * 0.62 and blue <= green * 0.48:
                yellow.add((x, y))

    spots = []
    while yellow:
        start = yellow.pop()
        component = [start]
        for x, y in component:
            for neighbor in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
                if neighbor in yellow:
                    yellow.remove(neighbor)
                    component.append(neighbor)
        if 4 <= len(component) <= 150:
            xs, ys = zip(*component)
            box_width, box_height = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
            inside = all((dx_squared[x] + dy_squared[y] if circle else max(dx_squared[x], dy_squared[y]))
                         <= 0.86 ** 2 for x, y in component)
            if (min(box_width, box_height) >= 2
                    and max(box_width, box_height) <= 1.7 * min(box_width, box_height)
                    and inside
                    and len(component) >= 0.55 * box_width * box_height
                    # Dim yellow details inside Gatherer icons can pass the shape checks.
                    and any(pixels[x, y][0] >= 220 and pixels[x, y][2] <= 80
                            for x, y in component)):
                spots.append((min(xs), min(ys), max(xs), max(ys)))
    return spots


def mark_detection(image, spots):
    marked = image.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for left, top, right, bottom in spots:
        draw.rectangle((left - 2, top - 2, right + 2, bottom + 2), outline="#ff38d5")
    return marked


def selection_bounds(origin, cursor):
    x, y = origin
    dx, dy = cursor[0] - x, cursor[1] - y
    side = min(abs(dx), abs(dy))
    end_x = x - side if dx < 0 else x + side
    end_y = y - side if dy < 0 else y + side
    return min(x, end_x), min(y, end_y), max(x, end_x), max(y, end_y)


class MinimapAlert:
    def __init__(self, root):
        self.root = root
        self.region = None
        self.region_shape = "circle"
        self.previous = self.candidate = self.repeats = 0
        self.running = False
        self.timer = self.hide_timer = None
        self.language = default_language()
        self.status = tk.StringVar()
        self.set_status("no_area")
        self.resource = tk.StringVar(value="ore")
        self.diagnostics = tk.BooleanVar(value=False)
        self.last_detection = None
        try:
            with Image.open(DETECTION_FILE) as saved:
                timestamp = strftime("%H:%M:%S", localtime(DETECTION_FILE.stat().st_mtime))
                self.last_detection = saved.copy(), timestamp
        except OSError:
            pass
        self.build_controls()

        self.alert = tk.Toplevel(root)
        self.alert.withdraw()
        self.alert.overrideredirect(True)
        self.alert.attributes("-topmost", True)
        self.alert.attributes("-disabled", True)
        tk.Frame(self.alert, bg=ACCENT, width=4).pack(side="left", fill="y")
        self.label = tk.Label(self.alert, bg=PANEL, fg=TEXT,
                              font=(FONT, 22, "bold"), padx=22, pady=12)
        self.label.pack()

    def tr(self, key, **values):
        return MESSAGES[self.language][key].format(**values)

    def set_status(self, key, **values):
        self.status_message = key, values
        self.update_status()

    def update_status(self):
        key, values = self.status_message
        if key == "selected":
            values = values | {"shape": self.tr(self.region_shape)}
        self.status.set(self.tr(key, **values))

    def change_language(self, label):
        self.language = next(code for code, name in LANGUAGES.items() if name == label)
        try:
            LANGUAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
            LANGUAGE_FILE.write_text(self.language, encoding="utf-8")
        except OSError:
            pass
        self.update_status()
        self.label.config(text=self.tr("nearby_" + self.resource.get()))
        self.controls.destroy()
        self.build_controls()

    def build_controls(self):
        root = self.root
        root.title(f"{APP_NAME} {VERSION}")
        icon_path = str(Path(__file__).parent / "assets" / "ScanMap.ico")
        root.iconbitmap(default=icon_path)
        root.iconbitmap(icon_path)
        root.configure(bg=BACKGROUND)
        root.minsize(450, 0)
        root.resizable(True, False)
        self.controls = tk.Frame(root, bg=BACKGROUND)
        self.controls.pack(fill="both", expand=True)
        root = self.controls

        heading = tk.Frame(root, bg=BACKGROUND)
        heading.pack(fill="x", padx=24, pady=(22, 18))
        icon = tk.Canvas(heading, width=46, height=46, bg=BACKGROUND, highlightthickness=0)
        icon.pack(side="left", padx=(0, 14))
        icon.create_oval(2, 2, 44, 44, fill=PANEL, outline="#607869")
        icon.create_oval(13, 13, 33, 33, outline="#607869")
        icon.create_line(23, 23, 36, 10, fill="#607869", width=2)
        icon.create_oval(26, 11, 34, 19, fill=ACCENT, outline="")
        tk.Label(heading, text=APP_NAME, bg=BACKGROUND, fg=TEXT,
                 font=(FONT, 20, "bold")).pack(anchor="w")
        tk.Label(heading, text=self.tr("subtitle"), bg=BACKGROUND, fg=MUTED,
                 font=(FONT, 10)).pack(anchor="w")

        preferences = tk.Frame(root, bg=BACKGROUND)
        preferences.pack(fill="x", padx=24, pady=(0, 12))
        tk.Label(preferences, text=VERSION, bg=BACKGROUND, fg=MUTED,
                 font=(FONT, 10)).pack(side="left")
        self.language_choice = tk.StringVar(value=LANGUAGES[self.language])
        picker = tk.OptionMenu(preferences, self.language_choice, *LANGUAGES.values(),
                               command=lambda label: root.after_idle(self.change_language, label))
        picker.config(bg=PANEL, fg=TEXT, activebackground="#405549", activeforeground=TEXT,
                      relief="flat", highlightbackground=BACKGROUND, highlightcolor=FOCUS,
                      highlightthickness=2, font=(FONT, 10), cursor="hand2", width=12)
        picker["menu"].config(bg=PANEL, fg=TEXT, activebackground="#405549", activeforeground=TEXT,
                              font=(FONT, 10))
        picker.pack(side="right")

        panel = tk.Frame(root, bg=PANEL, padx=16, pady=12)
        panel.pack(fill="x", padx=24)
        tk.Label(panel, text=self.tr("capture_area"), bg=PANEL, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w")
        tk.Label(panel, textvariable=self.status, bg=PANEL, fg=MUTED,
                 font=(FONT, 10), anchor="w", justify="left", wraplength=320).pack(fill="x", pady=(3, 8))
        shapes = tk.Frame(panel, bg=PANEL)
        shapes.pack(anchor="w")
        for shape in ("circle", "square"):
            tk.Button(shapes, text=self.tr("choose_" + shape), command=lambda value=shape: self.select_region(value),
                      bg="#2B3B31", fg=TEXT, activebackground="#405549", activeforeground=TEXT,
                      relief="flat", font=(FONT, 10, "bold"), padx=12, pady=5,
                      highlightbackground=PANEL, highlightcolor=FOCUS, highlightthickness=2,
                      cursor="hand2").pack(side="left", padx=(0, 6))

        settings = tk.Frame(root, bg=BACKGROUND)
        settings.pack(fill="x", padx=24, pady=(14, 12))
        tk.Label(settings, text=self.tr("tracking"), bg=BACKGROUND, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w")
        modes = tk.Frame(settings, bg=BACKGROUND)
        modes.pack(fill="x", pady=(6, 8))
        for resource in ("ore", "herb"):
            tk.Radiobutton(modes, text=self.tr(resource), value=resource, variable=self.resource,
                           bg="#26352C", fg=TEXT, activebackground="#405549", activeforeground=TEXT,
                           selectcolor="#405549", indicatoron=False, relief="flat",
                           font=(FONT, 10, "bold"), padx=16, pady=6,
                           highlightbackground=BACKGROUND, highlightcolor=FOCUS, highlightthickness=2,
                           cursor="hand2").pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Checkbutton(settings, text=self.tr("save"),
                       variable=self.diagnostics, bg=BACKGROUND, fg=MUTED,
                       activebackground=BACKGROUND, activeforeground=TEXT, selectcolor=PANEL,
                       highlightbackground=BACKGROUND, highlightcolor=FOCUS, highlightthickness=2,
                       font=(FONT, 10), cursor="hand2").pack(anchor="w")

        self.button = tk.Button(root, text=self.tr("stop" if self.running else "start"), command=self.toggle,
                                bg=ACCENT, fg=INK, activebackground="#FFE0A0", activeforeground=INK,
                                relief="flat", font=(FONT, 11, "bold"), pady=8,
                                highlightbackground=BACKGROUND, highlightcolor=FOCUS,
                                highlightthickness=2, cursor="hand2")
        self.button.pack(fill="x", padx=24, pady=(0, 20))

        history = tk.Frame(root, bg=PANEL, padx=16, pady=12)
        history.pack(fill="x", padx=24, pady=(0, 20))
        tk.Label(history, text=self.tr("last"), bg=PANEL, fg=TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w")
        self.preview = tk.Label(history, text=self.tr("no_detection"), bg=PANEL, fg=MUTED,
                                font=(FONT, 10), compound="bottom", justify="center")
        self.preview.pack(pady=(4, 0))
        if self.last_detection:
            self.show_detection(*self.last_detection)

    def show_detection(self, marked, time_text):
        self.last_detection = marked, time_text
        preview = marked.copy()
        preview.thumbnail((208, 208), Image.Resampling.NEAREST)
        self.preview.image = ImageTk.PhotoImage(preview)
        self.preview.config(image=self.preview.image, text=time_text)

    def select_region(self, shape):
        self.stop()
        self.root.withdraw()
        self.root.after(250, lambda: self.open_selector(shape))

    def open_selector(self, shape):
        window = tk.Toplevel(self.root)
        window.attributes("-fullscreen", True)
        window.attributes("-topmost", True)
        window.attributes("-alpha", 0.28)
        canvas = tk.Canvas(window, bg="black", cursor="crosshair", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        toolbar_window = tk.Toplevel(window)
        toolbar_window.overrideredirect(True)
        toolbar_window.attributes("-topmost", True)
        toolbar = tk.Frame(toolbar_window, bg=PANEL, padx=12, pady=8)
        toolbar.pack()
        instructions = tk.StringVar(value=self.tr("draw"))
        tk.Label(toolbar, textvariable=instructions, bg=PANEL, fg=TEXT,
                 font=(FONT, 12, "bold")).pack(side="left", padx=(0, 12))
        draw_outline = canvas.create_oval if shape == "circle" else canvas.create_rectangle
        outline = draw_outline(0, 0, 0, 0, outline="#ffdf47", width=4)
        origin = None
        moving_from = None
        pending = None

        def close():
            window.destroy()
            self.root.deiconify()
            if self.region is None:
                self.set_status("no_area")

        def start(event):
            nonlocal origin, moving_from
            origin = event.x, event.y
            moving_from = None
            if pending:
                left, top, right, bottom = pending
                if left <= event.x <= right and top <= event.y <= bottom:
                    moving_from = pending

        def show_bounds(bounds):
            nonlocal pending
            left, top, right, bottom = bounds
            side = min(right - left, canvas.winfo_width(), canvas.winfo_height())
            if side < 30:
                return
            left = max(0, min(left, canvas.winfo_width() - side))
            top = max(0, min(top, canvas.winfo_height() - side))
            pending = left, top, left + side, top + side
            canvas.coords(outline, *pending)
            instructions.set(self.tr("adjust", size=side))
            apply_button.config(state="normal", bg="#FFD371")

        def drag(event):
            if origin is None:
                return
            if moving_from:
                dx, dy = event.x - origin[0], event.y - origin[1]
                left, top, right, bottom = moving_from
                show_bounds((left + dx, top + dy, right + dx, bottom + dy))
            else:
                show_bounds(selection_bounds(origin, (event.x, event.y)))

        def finish(event):
            nonlocal origin, moving_from
            drag(event)
            origin = moving_from = None

        def nudge(dx, dy):
            if pending:
                left, top, right, bottom = pending
                show_bounds((left + dx, top + dy, right + dx, bottom + dy))

        def resize(step):
            if pending:
                left, top, right, bottom = pending
                limit = min(canvas.winfo_width(), canvas.winfo_height())
                if right - left + 2 * step <= limit:
                    show_bounds((left - step, top - step, right + step, bottom + step))

        def apply():
            if pending is None:
                return
            left, top, right, bottom = pending
            x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
            self.region = left + x, top + y, right + x, bottom + y
            self.region_shape = shape
            self.set_status("selected", size=right - left)
            close()

        apply_button = tk.Button(toolbar, text=self.tr("apply"), command=apply, state="disabled",
                                 bg="#344338", fg=INK, activebackground="#FFE4A8",
                                 activeforeground=INK, disabledforeground=MUTED, relief="flat",
                                 font=(FONT, 12, "bold"), padx=16, pady=6, cursor="hand2",
                                 highlightbackground=PANEL, highlightcolor=FOCUS, highlightthickness=2)
        apply_button.pack(side="left")
        canvas.bind("<ButtonPress-1>", start)
        canvas.bind("<B1-Motion>", drag)
        canvas.bind("<ButtonRelease-1>", finish)
        window.bind("<MouseWheel>", lambda event: resize(1 if event.delta > 0 else -1))
        window.bind("<plus>", lambda _: resize(1))
        window.bind("<minus>", lambda _: resize(-1))
        for key, dx, dy in (("Left", -1, 0), ("Right", 1, 0), ("Up", 0, -1), ("Down", 0, 1)):
            window.bind(f"<{key}>", lambda _, x=dx, y=dy: nudge(x, y))
        window.bind("<Return>", lambda _: apply())
        window.bind("<Escape>", lambda _: close())
        toolbar_window.bind("<Return>", lambda _: apply())
        toolbar_window.bind("<Escape>", lambda _: close())
        if self.region and self.region_shape == shape:
            def restore_outline(event):
                if event.width < 30 or event.height < 30:
                    return
                canvas.unbind("<Configure>")
                left, top, right, bottom = self.region
                x, y = canvas.winfo_rootx(), canvas.winfo_rooty()
                show_bounds((left - x, top - y, right - x, bottom - y))

            canvas.bind("<Configure>", restore_outline)
        window.update_idletasks()
        toolbar_window.geometry(f"+{canvas.winfo_rootx() + 20}+{canvas.winfo_rooty() + 20}")
        window.focus_force()
        toolbar_window.lift()

    def toggle(self):
        if self.running:
            self.stop()
        elif not self.region:
            messagebox.showinfo(APP_NAME, self.tr("select_first"))
        else:
            self.running = True
            self.previous = self.candidate = self.repeats = 0
            self.button.config(text=self.tr("stop"))
            self.set_status("scanning")
            self.root.iconify()
            self.timer = self.root.after(500, self.scan)

    def stop(self):
        self.running = False
        if self.timer is not None:
            self.root.after_cancel(self.timer)
            self.timer = None
        self.alert.withdraw()
        self.button.config(text=self.tr("start"))
        if self.region is not None:
            self.set_status("stopped")

    def scan(self):
        self.timer = None
        if not self.running:
            return
        if not game_is_active():
            self.repeats = 0
            self.timer = self.root.after(150, self.scan)
            return
        try:
            image = ImageGrab.grab(bbox=self.region)
        except OSError:
            self.stop()
            self.set_status("capture_error")
            return
        spots = yellow_spots(image, self.region_shape)
        count = len(spots)
        if count != self.candidate:
            self.candidate, self.repeats = count, 1
        else:
            self.repeats += 1
        if count > self.previous and self.repeats >= 3:
            self.previous = count
            self.notify()
            marked = mark_detection(image, spots)
            self.show_detection(marked, strftime("%H:%M:%S"))
            if self.diagnostics.get():
                try:
                    marked.resize((marked.width * 3, marked.height * 3), Image.Resampling.NEAREST).save(
                        DETECTION_FILE
                    )
                except OSError:
                    self.set_status("save_error")
        elif count < self.previous and self.repeats >= 5:
            self.previous = count
        self.timer = self.root.after(150, self.scan)

    def notify(self):
        self.label.config(text=self.tr("nearby_" + self.resource.get()))
        self.alert.update_idletasks()
        x = (self.root.winfo_screenwidth() - self.alert.winfo_reqwidth()) // 2
        y = round(self.root.winfo_screenheight() * 0.30)
        self.alert.geometry(f"+{x}+{y}")
        self.alert.deiconify()
        winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        if self.hide_timer is not None:
            self.root.after_cancel(self.hide_timer)
        self.hide_timer = self.root.after(1600, self.hide_alert)

    def hide_alert(self):
        self.hide_timer = None
        self.alert.withdraw()


if __name__ == "__main__":
    ctypes.windll.user32.SetProcessDPIAware()
    main_window = tk.Tk()
    MinimapAlert(main_window)
    main_window.mainloop()
