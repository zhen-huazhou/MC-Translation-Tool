"""
GUI 界面模块
使用 tkinter 构建清晰、可缩放的桌面界面。
"""

import json
import os
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Optional

from mod_scanner import ModScanner
from resourcepack import ResourcePackGenerator
from ftbquests import (
    FTBQuestsFolderGenerator,
    FTBQuestsImporter,
    FTBQuestsScanner,
    build_ftb_output_path,
    build_resourcepack_files,
    detect_ftbquests_directory,
    get_ftb_mode_label,
)
from translator import (
    TranslationCache,
    TranslationCancelled,
    TranslatorFactory,
)

APP_VERSION = "1.2.1"
COLORS = {
    "background": "#eef2f7",
    "card": "#ffffff",
    "header": "#172554",
    "header_subtitle": "#c7d2fe",
    "text": "#172033",
    "muted": "#64748b",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "border": "#dbe3ee",
    "success": "#15803d",
    "warning": "#c2410c",
    "error": "#b91c1c",
}


def _draw_rounded_rectangle(canvas, x1, y1, x2, y2, radius, **kwargs):
    """Draw a smooth rounded rectangle on a Tk canvas."""
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(
        points,
        smooth=True,
        splinesteps=24,
        **kwargs,
    )


class RoundedPanel(tk.Canvas):
    """A rounded card that keeps its content in a normal frame."""

    def __init__(
        self,
        parent,
        title="",
        padding=16,
        radius=14,
        parent_background=COLORS["background"],
    ):
        super().__init__(
            parent,
            background=parent_background,
            highlightthickness=0,
            borderwidth=0,
            height=1,
        )
        self._title = title
        self._padding = padding
        self._radius = radius
        self._parent_background = parent_background
        self._title_font = tkfont.Font(
            family="Microsoft YaHei UI", size=10, weight="bold"
        )
        self.content = ttk.Frame(self, style="Card.TFrame")
        self._window = self.create_window(
            padding,
            self._content_top(),
            anchor="nw",
            window=self.content,
        )
        self.bind("<Configure>", self._redraw)
        self.content.bind("<Configure>", self._sync_height)
        self.after_idle(self._sync_height)

    def _content_top(self):
        return 42 if self._title else self._padding

    def _sync_height(self, event=None):
        requested = (
            max(1, self.content.winfo_reqheight())
            + self._content_top()
            + self._padding
        )
        try:
            current = int(float(self.cget("height")))
        except (TypeError, ValueError, tk.TclError):
            current = 0
        if requested != current:
            self.configure(height=requested)

    def _redraw(self, event=None):
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self.delete("panel_bg")
        _draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            height - 1,
            self._radius,
            fill=COLORS["card"],
            outline=COLORS["border"],
            width=1,
            tags=("panel_bg",),
        )
        self.tag_lower("panel_bg", self._window)

        content_height = max(
            1,
            height - self._content_top() - self._padding,
        )
        self.coords(self._window, self._padding, self._content_top())
        self.itemconfigure(
            self._window,
            width=max(1, width - self._padding * 2),
            height=content_height,
        )

        if self._title:
            self.delete("panel_title")
            self.create_text(
                self._padding,
                20,
                text=self._title,
                anchor="w",
                fill=COLORS["text"],
                font=self._title_font,
                tags=("panel_title",),
            )


class RoundedNotebookHost(tk.Frame):
    """A naturally sized rounded frame that hosts a ttk.Notebook."""

    def __init__(
        self,
        parent,
        padding=2,
        radius=16,
        parent_background=COLORS["background"],
    ):
        super().__init__(
            parent,
            background=parent_background,
            highlightthickness=0,
            borderwidth=0,
        )
        self._padding = padding
        self._radius = radius
        self._canvas = tk.Canvas(
            self,
            background=parent_background,
            highlightthickness=0,
            borderwidth=0,
        )
        self._canvas.place(x=0, y=0, relwidth=1, relheight=1)

        self.notebook = ttk.Notebook(self, takefocus=False)
        self.notebook.grid(
            row=0,
            column=0,
            sticky="nsew",
            padx=padding,
            pady=padding,
        )
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.bind("<Configure>", self._redraw, add="+")

    def _redraw(self, event=None):
        width = max(2, self.winfo_width())
        height = max(2, self.winfo_height())
        self._canvas.delete("all")
        _draw_rounded_rectangle(
            self._canvas,
            1,
            1,
            width - 1,
            height - 1,
            self._radius,
            fill=COLORS["card"],
            outline=COLORS["border"],
            width=1,
        )


class RoundedButton(tk.Canvas):
    """A compact rounded button with clear hover and disabled states."""

    PALETTES = {
        "primary": {
            "normal": COLORS["accent"],
            "hover": COLORS["accent_hover"],
            "disabled": "#a9bad6",
            "outline": COLORS["accent"],
            "text": "#ffffff",
            "disabled_text": "#f4f7fb",
        },
        "secondary": {
            "normal": COLORS["card"],
            "hover": "#f1f5f9",
            "disabled": "#f5f7fa",
            "outline": COLORS["border"],
            "text": COLORS["text"],
            "disabled_text": "#9aa5b4",
        },
    }

    def __init__(
        self,
        parent,
        text,
        command=None,
        variant="secondary",
        state=tk.NORMAL,
        height=34,
        background=COLORS["card"],
        font_size=10,
        bold=False,
    ):
        self._font = tkfont.Font(
            family="Microsoft YaHei UI",
            size=font_size,
            weight="bold" if bold else "normal",
        )
        self._text = text
        self._command = command
        self._variant = variant
        self._enabled = str(state) != str(tk.DISABLED)
        self._hovered = False
        self._radius = 8
        width = max(74, self._font.measure(text) + 30)
        super().__init__(
            parent,
            width=width,
            height=height,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2" if self._enabled else "arrow",
            takefocus=1,
        )
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Key-space>", self._on_space)
        self._draw()

    def _draw(self):
        self.delete("all")
        palette = self.PALETTES[self._variant]
        if self._enabled:
            fill = palette["hover"] if self._hovered else palette["normal"]
            outline = palette["outline"]
            text_color = palette["text"]
        else:
            fill = palette["disabled"]
            outline = COLORS["border"]
            text_color = palette["disabled_text"]

        _draw_rounded_rectangle(
            self,
            1,
            1,
            self.winfo_reqwidth() - 1,
            self.winfo_reqheight() - 1,
            self._radius,
            fill=fill,
            outline=outline,
            width=1,
        )
        self.create_text(
            self.winfo_reqwidth() / 2,
            self.winfo_reqheight() / 2,
            text=self._text,
            fill=text_color,
            font=self._font,
        )

    def set_state(self, state):
        self._enabled = str(state) != str(tk.DISABLED)
        self.configure(cursor="hand2" if self._enabled else "arrow")
        self._draw()

    def config(self, cnf=None, **kwargs):
        if cnf is not None:
            if isinstance(cnf, dict):
                kwargs.update(cnf)
            else:
                return super().config(cnf, **kwargs)
        state = kwargs.pop("state", None)
        if state is not None:
            self.set_state(state)
        if kwargs:
            super().config(**kwargs)

    configure = config

    def _on_enter(self, event=None):
        if self._enabled:
            self._hovered = True
            self._draw()

    def _on_leave(self, event=None):
        self._hovered = False
        self._draw()

    def _on_release(self, event=None):
        if not self._enabled:
            return
        self.focus_set()
        self._hovered = False
        self._draw()
        if self._command:
            self._command()

    def _on_space(self, event=None):
        if self._enabled and self._command:
            self._command()
            return "break"


class RoundedCheckbutton(tk.Canvas):
    """A rounded checkbox whose selected state is an explicit check mark."""

    def __init__(
        self,
        parent,
        text,
        variable,
        background=COLORS["card"],
        font_size=10,
    ):
        self._variable = variable
        self._font = tkfont.Font(
            family="Microsoft YaHei UI", size=font_size
        )
        width = self._font.measure(text) + 48
        super().__init__(
            parent,
            width=width,
            height=32,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            cursor="hand2",
            takefocus=1,
        )
        self._text = text
        self._text_x = 30
        self._background = background
        self.bind("<ButtonRelease-1>", self._toggle)
        self.bind("<Key-space>", self._toggle)
        self._trace = self._variable.trace_add("write", self._on_variable)
        self._draw()

    def _on_variable(self, *args):
        self._draw()

    def _draw(self):
        self.delete("all")
        selected = bool(self._variable.get())
        if selected:
            fill = COLORS["accent"]
            outline = COLORS["accent"]
        else:
            fill = COLORS["card"]
            outline = "#8fa0b8"

        _draw_rounded_rectangle(
            self,
            2,
            7,
            21,
            26,
            5,
            fill=fill,
            outline=outline,
            width=1,
        )
        if selected:
            self.create_line(
                6,
                16,
                10,
                21,
                18,
                11,
                fill="#ffffff",
                width=2,
                capstyle=tk.ROUND,
                joinstyle=tk.ROUND,
            )
        self.create_text(
            self._text_x,
            16,
            text=self._text,
            anchor="w",
            fill=COLORS["text"],
            font=self._font,
        )

    def _toggle(self, event=None):
        self.focus_set()
        self._variable.set(not bool(self._variable.get()))
        return "break"



class RoundedProgressbar(tk.Canvas):
    """A rounded progress bar backed by a Tk variable."""

    def __init__(
        self,
        parent,
        variable,
        maximum=100,
        background=COLORS["card"],
        height=12,
    ):
        self._variable = variable
        self._maximum = max(1.0, float(maximum))
        self._height = height
        super().__init__(
            parent,
            height=height,
            background=background,
            highlightthickness=0,
            borderwidth=0,
        )
        self._trace = self._variable.trace_add("write", self._draw)
        self.bind("<Configure>", self._draw)
        self._draw()

    def _draw(self, event=None):
        self.delete("all")
        width = max(2, self.winfo_width())
        if width <= 2:
            width = max(120, self.winfo_reqwidth())
        radius = self._height / 2
        _draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            self._height - 1,
            radius,
            fill="#dbe4f0",
            outline="#dbe4f0",
            width=1,
        )

        try:
            value = float(self._variable.get())
        except (TypeError, ValueError, tk.TclError):
            value = 0.0
        ratio = max(0.0, min(1.0, value / self._maximum))
        if ratio <= 0:
            return
        fill_width = max(2, 2 + (width - 4) * ratio)
        _draw_rounded_rectangle(
            self,
            1,
            1,
            min(width - 1, fill_width),
            self._height - 1,
            radius,
            fill=COLORS["accent"],
            outline=COLORS["accent"],
            width=1,
        )


class RoundedEntry(tk.Canvas):
    """A rounded text field with a borderless native entry inside."""

    def __init__(
        self,
        parent,
        textvariable,
        show=None,
        background=COLORS["card"],
        width=180,
    ):
        self._background = background
        self._focused = False
        self._font = tkfont.Font(family="Microsoft YaHei UI", size=10)
        super().__init__(
            parent,
            width=width,
            height=36,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=1,
        )
        self.entry = tk.Entry(
            self,
            textvariable=textvariable,
            show=show or "",
            relief=tk.FLAT,
            borderwidth=0,
            highlightthickness=0,
            font=self._font,
            bg=COLORS["card"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            selectbackground=COLORS["accent"],
            selectforeground="#ffffff",
        )
        self._window = self.create_window(
            12,
            18,
            anchor="w",
            window=self.entry,
        )
        self.bind("<Button-1>", lambda event: self.entry.focus_set())
        self.bind("<Configure>", self._redraw)
        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)
        self._redraw()

    def _on_focus_in(self, event=None):
        self._focused = True
        self._redraw()

    def _on_focus_out(self, event=None):
        self._focused = False
        self._redraw()

    def _redraw(self, event=None):
        width = max(2, self.winfo_width())
        if width <= 2:
            width = max(120, self.winfo_reqwidth())
        self.delete("field_bg")
        outline = COLORS["accent"] if self._focused else COLORS["border"]
        _draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            35,
            8,
            fill=COLORS["card"],
            outline=outline,
            width=1,
            tags=("field_bg",),
        )
        self.tag_lower("field_bg", self._window)
        self.coords(self._window, 12, 18)
        self.itemconfigure(self._window, width=max(1, width - 22), height=22)


class RoundedCombobox(tk.Canvas):
    """A rounded border around a compact ttk combobox."""

    def __init__(
        self,
        parent,
        textvariable,
        values,
        state="readonly",
        background=COLORS["card"],
        width=180,
    ):
        self._background = background
        self._focused = False
        super().__init__(
            parent,
            width=width,
            height=36,
            background=background,
            highlightthickness=0,
            borderwidth=0,
        )
        self.combobox = ttk.Combobox(
            self,
            textvariable=textvariable,
            values=values,
            state=state,
            style="Rounded.TCombobox",
            width=1,
        )
        self._window = self.create_window(
            10,
            18,
            anchor="w",
            window=self.combobox,
        )
        self.bind("<Configure>", self._redraw)
        self.combobox.bind("<FocusIn>", self._on_focus_in)
        self.combobox.bind("<FocusOut>", self._on_focus_out)
        self._redraw()

    def bind(self, sequence=None, func=None, add=None):
        if sequence == "<<ComboboxSelected>>":
            return self.combobox.bind(sequence, func, add)
        return super().bind(sequence, func, add)

    def _on_focus_in(self, event=None):
        self._focused = True
        self._redraw()

    def _on_focus_out(self, event=None):
        self._focused = False
        self._redraw()

    def _redraw(self, event=None):
        width = max(2, self.winfo_width())
        if width <= 2:
            width = max(120, self.winfo_reqwidth())
        self.delete("field_bg")
        outline = COLORS["accent"] if self._focused else COLORS["border"]
        _draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            35,
            8,
            fill=COLORS["card"],
            outline=outline,
            width=1,
            tags=("field_bg",),
        )
        self.tag_lower("field_bg", self._window)
        self.coords(self._window, 10, 18)
        self.itemconfigure(self._window, width=max(1, width - 20), height=28)


class RoundedSpinbox(tk.Canvas):
    """A rounded border around a ttk spinbox."""

    def __init__(
        self,
        parent,
        textvariable,
        from_,
        to,
        state=tk.NORMAL,
        background=COLORS["card"],
        width=84,
    ):
        self._background = background
        self._focused = False
        super().__init__(
            parent,
            width=width,
            height=36,
            background=background,
            highlightthickness=0,
            borderwidth=0,
        )
        self.spinbox = ttk.Spinbox(
            self,
            from_=from_,
            to=to,
            textvariable=textvariable,
            state=state,
            style="Rounded.TSpinbox",
            width=1,
        )
        self._window = self.create_window(
            10,
            18,
            anchor="w",
            window=self.spinbox,
        )
        self.bind("<Configure>", self._redraw)
        self.spinbox.bind("<FocusIn>", self._on_focus_in)
        self.spinbox.bind("<FocusOut>", self._on_focus_out)
        self._redraw()

    def config(self, cnf=None, **kwargs):
        if cnf is not None:
            if isinstance(cnf, dict):
                kwargs.update(cnf)
            else:
                self.spinbox.config(cnf)
        if "state" in kwargs:
            self.spinbox.config(state=kwargs.pop("state"))
        if kwargs:
            self.spinbox.config(**kwargs)
        return None

    configure = config

    def _on_focus_in(self, event=None):
        self._focused = True
        self._redraw()

    def _on_focus_out(self, event=None):
        self._focused = False
        self._redraw()

    def _redraw(self, event=None):
        width = max(2, self.winfo_width())
        if width <= 2:
            width = max(70, self.winfo_reqwidth())
        self.delete("field_bg")
        outline = COLORS["accent"] if self._focused else COLORS["border"]
        _draw_rounded_rectangle(
            self,
            1,
            1,
            width - 1,
            35,
            8,
            fill=COLORS["card"],
            outline=outline,
            width=1,
            tags=("field_bg",),
        )
        self.tag_lower("field_bg", self._window)
        self.coords(self._window, 10, 18)
        self.itemconfigure(self._window, width=max(1, width - 20), height=28)


class TranslationApp:
    """MC 整合包汉化工具主应用。"""

    def __init__(self, root):
        self.root = root
        self.root.title(f"Minecraft 整合包汉化工具 v{APP_VERSION}")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1080, max(860, screen_width - 100))
        window_height = min(820, max(640, screen_height - 120))
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height) // 2 - 20)
        self.root.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.root.minsize(min(860, window_width), min(640, window_height))
        self.root.configure(bg=COLORS["background"])

        self.mods_path = tk.StringVar()
        self.ftb_quests_path = tk.StringVar()
        self.ftb_auto_detect = tk.BooleanVar(value=True)
        self.ftb_auto_import = tk.BooleanVar(value=False)
        self.output_path = tk.StringVar(value="./汉化补丁.zip")
        self.api_type = tk.StringVar(value="ai")

        self.ai_api_key = tk.StringVar()
        self.ai_base_url = tk.StringVar(value="https://api.deepseek.com/v1")
        self.ai_model = tk.StringVar(value="deepseek-chat")
        self.baidu_app_id = tk.StringVar()
        self.baidu_secret_key = tk.StringVar()
        self.deepl_auth_key = tk.StringVar()

        self.use_cache = tk.BooleanVar(value=True)
        self.skip_existing = tk.BooleanVar(value=True)
        self.mc_version = tk.StringVar(value="1.20-1.20.2")
        self.pack_format = tk.IntVar(value=15)
        self.batch_size = tk.IntVar(value=10)
        self.max_workers = tk.IntVar(value=3)

        self.progress_var = tk.DoubleVar(value=0)
        self.status_var = tk.StringVar(value="就绪")
        self.is_running = False
        self.current_thread: Optional[threading.Thread] = None
        self._closing = False
        self._ui_queue = queue.Queue()

        self._configure_styles()
        self._create_widgets()
        self.load_config()
        self.mods_path.trace_add("write", self._on_translation_path_changed)
        self.ftb_quests_path.trace_add(
            "write", self._on_translation_path_changed
        )
        self._update_start_button_state()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        self._poll_after_id = self.root.after(80, self._poll_ui_queue)

    def _configure_styles(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure(
            ".",
            font=("Microsoft YaHei UI", 10),
            foreground=COLORS["text"],
            background=COLORS["card"],
        )
        style.configure("App.TFrame", background=COLORS["background"])
        style.configure("Card.TFrame", background=COLORS["card"])
        style.configure(
            "TLabel",
            background=COLORS["card"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["card"],
            foreground=COLORS["muted"],
        )
        style.configure(
            "App.TLabel",
            background=COLORS["background"],
            foreground=COLORS["text"],
        )
        style.configure(
            "TNotebook",
            background=COLORS["card"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        style.configure(
            "TNotebook.Tab",
            padding=(20, 10),
            borderwidth=0,
            focuscolor=COLORS["card"],
            focusfill=COLORS["card"],
            font=("Microsoft YaHei UI", 10),
        )
        style.map(
            "TNotebook.Tab",
            background=[
                ("selected", COLORS["card"]),
                ("active", "#e3eaf5"),
                ("!selected", "#e8edf5"),
            ],
            foreground=[
                ("selected", COLORS["accent"]),
                ("!selected", COLORS["muted"]),
            ],
            expand=[("selected", (0, 0, 0, 0))],
        )
        style.configure(
            "TEntry",
            padding=(8, 7),
            fieldbackground=COLORS["card"],
            background=COLORS["card"],
            foreground=COLORS["text"],
            bordercolor=COLORS["border"],
            lightcolor=COLORS["border"],
            darkcolor=COLORS["border"],
        )
        style.map(
            "TEntry",
            bordercolor=[("focus", COLORS["accent"])],
            lightcolor=[("focus", COLORS["accent"])],
            darkcolor=[("focus", COLORS["accent"])],
        )
        style.configure(
            "Rounded.TCombobox",
            padding=(4, 2),
            fieldbackground=COLORS["card"],
            background=COLORS["card"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["card"],
            lightcolor=COLORS["card"],
            darkcolor=COLORS["card"],
        )
        style.map(
            "Rounded.TCombobox",
            fieldbackground=[
                ("readonly", COLORS["card"]),
                ("disabled", "#f1f5f9"),
            ],
            foreground=[
                ("readonly", COLORS["text"]),
                ("disabled", COLORS["muted"]),
            ],
            selectbackground=[("readonly", COLORS["card"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "Rounded.TSpinbox",
            padding=(4, 2),
            fieldbackground=COLORS["card"],
            background=COLORS["card"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
            bordercolor=COLORS["card"],
            lightcolor=COLORS["card"],
            darkcolor=COLORS["card"],
        )
        style.map(
            "Rounded.TSpinbox",
            fieldbackground=[("disabled", "#f1f5f9")],
            foreground=[("disabled", COLORS["muted"])],
        )
        style.configure("TButton", padding=(12, 7))

    def _clear_notebook_tab_focus(self, event=None):
        def focus_page():
            selected = self.notebook.select()
            if selected:
                self.root.nametowidget(selected).focus_set()

        self.root.after_idle(focus_page)

    def _create_widgets(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        body = ttk.Frame(
            self.root,
            style="App.TFrame",
            padding=(16, 16, 16, 10),
        )
        body.grid(row=0, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        notebook_host = RoundedNotebookHost(
            body,
            padding=2,
            radius=16,
            parent_background=COLORS["background"],
        )
        notebook_host.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.notebook = notebook_host.notebook
        self.notebook.bind(
            "<<NotebookTabChanged>>",
            self._clear_notebook_tab_focus,
            add="+",
        )

        basic_tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        api_tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        advanced_tab = ttk.Frame(self.notebook, style="Card.TFrame", padding=16)
        self.notebook.add(basic_tab, text="基本设置")
        self.notebook.add(api_tab, text="翻译服务")
        self.notebook.add(advanced_tab, text="高级设置")

        self._build_basic_tab(basic_tab)
        self._build_api_tab(api_tab)
        self._build_advanced_tab(advanced_tab)
        self._build_log_area(body)
        self._build_footer()
    def _build_basic_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        paths = RoundedPanel(parent, title="目录与输出", padding=16)
        paths.grid(row=0, column=0, sticky="ew")
        paths_content = paths.content
        paths_content.columnconfigure(1, weight=1)

        ttk.Label(paths_content, text="Mods 文件夹").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=5
        )
        RoundedEntry(
            paths_content,
            textvariable=self.mods_path,
        ).grid(row=0, column=1, sticky="ew", pady=5)
        RoundedButton(
            paths_content,
            text="浏览",
            command=self.browse_mods_folder,
        ).grid(row=0, column=2, padx=(10, 0), pady=5)

        ttk.Label(paths_content, text="输出文件").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=5
        )
        RoundedEntry(
            paths_content,
            textvariable=self.output_path,
        ).grid(row=1, column=1, sticky="ew", pady=5)
        RoundedButton(
            paths_content,
            text="选择",
            command=self.browse_output_path,
        ).grid(row=1, column=2, padx=(10, 0), pady=5)

        ttk.Label(paths_content, text="FTB Quests 目录").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=5
        )
        RoundedEntry(
            paths_content,
            textvariable=self.ftb_quests_path,
        ).grid(row=2, column=1, sticky="ew", pady=5)
        RoundedButton(
            paths_content,
            text="浏览",
            command=self.browse_ftb_quests_folder,
        ).grid(row=2, column=2, padx=(10, 0), pady=5)
        ttk.Label(
            paths_content,
            text="路径为空时是否自动检测，由高级设置控制。",
            style="Muted.TLabel",
        ).grid(row=3, column=1, columnspan=2, sticky="w", pady=(0, 3))

        version_frame = RoundedPanel(
            parent,
            title="游戏版本与输出格式",
            padding=16,
        )
        version_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        version_content = version_frame.content
        version_content.columnconfigure(1, weight=1)

        ttk.Label(version_content, text="Minecraft 版本").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=5
        )
        version_combo = RoundedCombobox(
            version_content,
            textvariable=self.mc_version,
            state="readonly",
            values=(
                "1.21.4",
                "1.21.2-1.21.3",
                "1.20.5-1.21.1",
                "1.20.3-1.20.4",
                "1.20-1.20.2",
                "1.19.4",
                "1.19.3",
                "1.19-1.19.2",
                "1.18-1.18.2",
                "1.17-1.17.1",
                "1.16.2-1.16.5",
                "其他/手动设置",
            ),
            width=360,
        )
        version_combo.grid(row=0, column=1, sticky="ew", pady=5)
        version_combo.bind("<<ComboboxSelected>>", self.on_mc_version_changed)

        ttk.Label(version_content, text="pack_format").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=5
        )
        pack_frame = ttk.Frame(version_content, style="Card.TFrame")
        pack_frame.grid(row=1, column=1, sticky="w", pady=5)
        self.pack_format_spinbox = RoundedSpinbox(
            pack_frame,
            from_=1,
            to=80,
            textvariable=self.pack_format,
            width=82,
            state="disabled",
        )
        self.pack_format_spinbox.pack(side="left")
        self.pack_format_hint = ttk.Label(
            pack_frame,
            text="用于匹配 Minecraft 资源包版本",
            style="Muted.TLabel",
        )
        self.pack_format_hint.pack(side="left", padx=(10, 0))
    def _build_api_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        selector = RoundedPanel(parent, padding=14)
        selector.grid(row=0, column=0, sticky="ew")
        selector_content = selector.content
        selector_content.columnconfigure(1, weight=1)

        ttk.Label(selector_content, text="翻译服务").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        service_combo = RoundedCombobox(
            selector_content,
            textvariable=self.api_type,
            state="readonly",
            values=("ai", "baidu", "deepl"),
            width=210,
        )
        service_combo.grid(row=0, column=1, sticky="w")
        service_combo.bind("<<ComboboxSelected>>", self.on_api_type_changed)
        ttk.Label(
            selector_content,
            text="AI 适合批量翻译，DeepL 质量高，百度适合低成本调用。",
            style="Muted.TLabel",
        ).grid(row=0, column=2, sticky="e")

        config_panel = RoundedPanel(parent, title="服务配置", padding=16)
        config_panel.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        container = config_panel.content
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.ai_config_frame = ttk.Frame(container, style="Card.TFrame")
        self.baidu_config_frame = ttk.Frame(container, style="Card.TFrame")
        self.deepl_config_frame = ttk.Frame(container, style="Card.TFrame")
        for frame in (
            self.ai_config_frame,
            self.baidu_config_frame,
            self.deepl_config_frame,
        ):
            frame.grid(row=0, column=0, sticky="nsew")

        self._build_ai_config(self.ai_config_frame)
        self._build_baidu_config(self.baidu_config_frame)
        self._build_deepl_config(self.deepl_config_frame)
        self.on_api_type_changed()

    def _build_ai_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="API Key").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.ai_api_key,
            show="*",
        ).grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(parent, text="Base URL").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.ai_base_url,
        ).grid(row=1, column=1, sticky="ew", pady=7)
        ttk.Label(parent, text="Model").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.ai_model,
        ).grid(row=2, column=1, sticky="ew", pady=7)
        ttk.Label(
            parent,
            text="默认配置兼容 DeepSeek，也可填写其他 OpenAI Chat Completions 接口。",
            style="Muted.TLabel",
        ).grid(row=3, column=1, sticky="w", pady=(4, 0))

    def _build_baidu_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="APP ID").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.baidu_app_id,
        ).grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(parent, text="Secret Key").grid(
            row=1, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.baidu_secret_key,
            show="*",
        ).grid(row=1, column=1, sticky="ew", pady=7)

    def _build_deepl_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Auth Key").grid(
            row=0, column=0, sticky="w", padx=(0, 12), pady=7
        )
        RoundedEntry(
            parent,
            textvariable=self.deepl_auth_key,
            show="*",
        ).grid(row=0, column=1, sticky="ew", pady=7)
        ttk.Label(
            parent,
            text="默认使用 DeepL Free API 地址。",
            style="Muted.TLabel",
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))
    def _build_ai_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="API Key").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.ai_api_key, show="*").grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(parent, text="Base URL").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.ai_base_url).grid(
            row=1, column=1, sticky="ew", pady=6
        )
        ttk.Label(parent, text="Model").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.ai_model).grid(
            row=2, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            parent,
            text="默认配置兼容 DeepSeek；也可填写其他 OpenAI Chat Completions 接口。",
            foreground=COLORS["muted"],
        ).grid(row=3, column=1, sticky="w", pady=(2, 0))

    def _build_baidu_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="APP ID").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.baidu_app_id).grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(parent, text="Secret Key").grid(
            row=1, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.baidu_secret_key, show="*").grid(
            row=1, column=1, sticky="ew", pady=6
        )

    def _build_deepl_config(self, parent):
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Auth Key").grid(
            row=0, column=0, sticky="w", padx=(0, 10), pady=6
        )
        ttk.Entry(parent, textvariable=self.deepl_auth_key, show="*").grid(
            row=0, column=1, sticky="ew", pady=6
        )
        ttk.Label(
            parent,
            text="默认使用 DeepL Free API 地址。",
            foreground=COLORS["muted"],
        ).grid(row=1, column=1, sticky="w", pady=(4, 0))

    def _build_advanced_tab(self, parent):
        parent.columnconfigure(0, weight=1)

        settings = RoundedPanel(parent, title="翻译行为", padding=16)
        settings.grid(row=0, column=0, sticky="ew")
        content = settings.content
        content.columnconfigure(1, weight=1)

        self.use_cache_checkbox = RoundedCheckbutton(
            content,
            text="使用翻译缓存，避免重复请求和费用",
            variable=self.use_cache,
        )
        self.use_cache_checkbox.grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )

        self.skip_existing_checkbox = RoundedCheckbutton(
            content,
            text="智能补全部分汉化的模组，完整汉化仍跳过",
            variable=self.skip_existing,
        )
        self.skip_existing_checkbox.grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )

        self.ftb_auto_detect_checkbox = RoundedCheckbutton(
            content,
            text="自动检测 FTB Quests 目录（任务书路径为空时）",
            variable=self.ftb_auto_detect,
        )
        self.ftb_auto_detect_checkbox.grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 5)
        )

        self.ftb_auto_import_checkbox = RoundedCheckbutton(
            content,
            text="内联/外部语言任务书生成后，自动备份并替换当前 quests 目录",
            variable=self.ftb_auto_import,
        )
        self.ftb_auto_import_checkbox.grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )

        ttk.Label(content, text="批量大小").grid(
            row=4, column=0, sticky="w", pady=5
        )
        RoundedSpinbox(
            content,
            from_=1,
            to=50,
            textvariable=self.batch_size,
            width=82,
        ).grid(row=4, column=1, sticky="w", padx=(12, 0), pady=5)
        ttk.Label(
            content,
            text="AI/DeepL 每批处理条数，建议 10-20。",
            style="Muted.TLabel",
        ).grid(row=4, column=2, sticky="w", padx=(14, 0), pady=5)

        ttk.Label(content, text="并发请求数").grid(
            row=5, column=0, sticky="w", pady=5
        )
        RoundedSpinbox(
            content,
            from_=1,
            to=8,
            textvariable=self.max_workers,
            width=82,
        ).grid(row=5, column=1, sticky="w", padx=(12, 0), pady=5)
        ttk.Label(
            content,
            text="建议 2-4；过高可能触发 API 限流。",
            style="Muted.TLabel",
        ).grid(row=5, column=2, sticky="w", padx=(14, 0), pady=5)

    def _build_log_area(self, parent):
        log_card = RoundedPanel(parent, title="运行日志", padding=14)
        log_card.grid(row=1, column=0, sticky="nsew")
        content = log_card.content
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        log_header = ttk.Frame(content, style="Card.TFrame")
        log_header.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        log_header.columnconfigure(0, weight=1)
        ttk.Label(
            log_header,
            text="扫描、翻译、打包状态会显示在这里。",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w")
        RoundedButton(
            log_header,
            text="清空日志",
            command=self.clear_log,
        ).grid(row=0, column=1, sticky="e")

        self.log_text = scrolledtext.ScrolledText(
            content,
            height=9,
            wrap=tk.WORD,
            state=tk.DISABLED,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=10,
            pady=8,
            bg=COLORS["card"],
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            font=("Microsoft YaHei UI", 9),
        )
        self.log_text.grid(row=1, column=0, sticky="nsew")
        self.log_text.tag_config("info", foreground=COLORS["text"])
        self.log_text.tag_config("success", foreground=COLORS["success"])
        self.log_text.tag_config("warning", foreground=COLORS["warning"])
        self.log_text.tag_config("error", foreground=COLORS["error"])
    def _build_footer(self):
        footer = tk.Frame(self.root, bg=COLORS["card"])
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)

        separator = tk.Frame(footer, bg=COLORS["border"], height=1)
        separator.grid(row=0, column=0, sticky="ew")

        toolbar = tk.Frame(footer, bg=COLORS["card"], padx=16, pady=10)
        toolbar.grid(row=1, column=0, sticky="ew")
        toolbar.columnconfigure(0, weight=1)

        tk.Label(
            toolbar,
            textvariable=self.status_var,
            bg=COLORS["card"],
            fg=COLORS["muted"],
            font=("Microsoft YaHei UI", 10),
        ).grid(row=0, column=0, sticky="w")

        self.open_output_button = RoundedButton(
            toolbar,
            text="打开输出目录",
            command=self.open_output_folder,
        )
        self.open_output_button.grid(row=0, column=1, padx=4)

        RoundedButton(
            toolbar,
            text="保存配置",
            command=self.save_config,
        ).grid(row=0, column=2, padx=4)

        self.clear_cache_button = RoundedButton(
            toolbar,
            text="清空缓存",
            command=self.clear_cache,
        )
        self.clear_cache_button.grid(row=0, column=3, padx=4)

        self.stop_button = RoundedButton(
            toolbar,
            text="停止",
            command=self.stop_translation,
            state=tk.DISABLED,
        )
        self.stop_button.grid(row=0, column=4, padx=4)

        self.start_button = RoundedButton(
            toolbar,
            text="开始汉化",
            command=self.start_translation,
            variant="primary",
            bold=True,
        )
        self.start_button.grid(row=0, column=5, padx=(4, 0))

        progress_row = tk.Frame(
            footer,
            bg=COLORS["card"],
            padx=16,
            pady=0,
        )
        progress_row.grid(row=2, column=0, sticky="ew")
        progress_row.columnconfigure(0, weight=1)
        self.progress_bar = RoundedProgressbar(
            progress_row,
            variable=self.progress_var,
            maximum=100,
            background=COLORS["card"],
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 12))

    def _on_translation_path_changed(self, *args):
        """路径变化时同步开始按钮状态。"""
        self._update_start_button_state()

    def _update_start_button_state(self):
        """仅当至少提供一个汉化数据源时启用开始按钮。"""
        if not hasattr(self, "start_button"):
            return
        has_source = bool(
            self.mods_path.get().strip()
            or self.ftb_quests_path.get().strip()
        )
        state = (
            tk.NORMAL
            if has_source and not self.is_running
            else tk.DISABLED
        )
        self.start_button.config(state=state)

    def browse_mods_folder(self):
        folder = filedialog.askdirectory(title="选择 Mods 文件夹")
        if folder:
            self.mods_path.set(folder)
            if (
                self.ftb_auto_detect.get()
                and not self.ftb_quests_path.get().strip()
            ):
                detected = detect_ftbquests_directory(folder)
                if detected:
                    self.ftb_quests_path.set(str(detected))

    def browse_ftb_quests_folder(self):
        folder = filedialog.askdirectory(
            title="选择 FTB Quests 的 quests 目录"
        )
        if folder:
            self.ftb_quests_path.set(folder)

    def browse_output_path(self):
        file_path = filedialog.asksaveasfilename(
            title="选择资源包输出位置",
            defaultextension=".zip",
            filetypes=[("ZIP 资源包", "*.zip"), ("所有文件", "*.*")],
        )
        if file_path:
            self.output_path.set(file_path)

    def on_mc_version_changed(self, event=None):
        version_map = {
            "1.21.4": 46,
            "1.21.2-1.21.3": 42,
            "1.20.5-1.21.1": 34,
            "1.20.3-1.20.4": 22,
            "1.20-1.20.2": 15,
            "1.19.4": 13,
            "1.19.3": 12,
            "1.19-1.19.2": 9,
            "1.18-1.18.2": 8,
            "1.17-1.17.1": 7,
            "1.16.2-1.16.5": 6,
        }
        version = self.mc_version.get()
        if version in version_map:
            self.pack_format.set(version_map[version])
            self.pack_format_spinbox.config(state="disabled")
            self.pack_format_hint.config(
                text=f"已自动匹配 pack_format {version_map[version]}"
            )
        else:
            self.pack_format_spinbox.config(state="normal")
            self.pack_format_hint.config(text="请手动填写对应版本")

    def on_api_type_changed(self, event=None):
        frames = {
            "ai": self.ai_config_frame,
            "baidu": self.baidu_config_frame,
            "deepl": self.deepl_config_frame,
        }
        for frame in frames.values():
            frame.grid_remove()
        frames.get(self.api_type.get(), self.ai_config_frame).grid()

    def _queue_event(self, event_type: str, **payload):
        self._ui_queue.put((event_type, payload))

    def _poll_ui_queue(self):
        if self._closing:
            return
        try:
            while True:
                event_type, payload = self._ui_queue.get_nowait()
                if event_type == "log":
                    self._append_log(payload["message"], payload["level"])
                elif event_type == "progress":
                    self.progress_var.set(payload["value"])
                elif event_type == "status":
                    self.status_var.set(payload["value"])
                elif event_type == "done":
                    self._set_running_state(False)
        except queue.Empty:
            pass
        self._poll_after_id = self.root.after(80, self._poll_ui_queue)

    def log(self, message: str, level: str = "info"):
        self._queue_event("log", message=message, level=level)

    def _append_log(self, message: str, level: str = "info"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"{message}\n", level)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def clear_log(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _set_running_state(self, running: bool):
        self.is_running = running
        if running:
            self.start_button.config(state=tk.DISABLED)
        else:
            self._update_start_button_state()
        self.stop_button.config(state=tk.NORMAL if running else tk.DISABLED)
        self.clear_cache_button.config(
            state=tk.DISABLED if running else tk.NORMAL
        )

    def start_translation(self):
        if self.is_running:
            return

        mods_path = self.mods_path.get().strip()
        output_path = self.output_path.get().strip()
        ftb_quests_path = self.ftb_quests_path.get().strip()
        if not mods_path and not ftb_quests_path:
            messagebox.showerror(
                "缺少汉化目录",
                "请至少选择 Mods 文件夹或 FTB Quests 目录。",
            )
            return
        if not output_path:
            messagebox.showerror("缺少输出路径", "请先选择输出文件。")
            return

        api_type = self.api_type.get()
        if api_type == "ai" and not self.ai_api_key.get().strip():
            messagebox.showerror("缺少 API Key", "请填写 AI API Key。")
            return
        if api_type == "baidu" and (
            not self.baidu_app_id.get().strip()
            or not self.baidu_secret_key.get().strip()
        ):
            messagebox.showerror("缺少百度配置", "请填写 APP ID 和 Secret Key。")
            return
        if api_type == "deepl" and not self.deepl_auth_key.get().strip():
            messagebox.showerror("缺少 DeepL 配置", "请填写 DeepL Auth Key。")
            return

        if (
            self.ftb_auto_detect.get()
            and mods_path
            and not ftb_quests_path
        ):
            detected = detect_ftbquests_directory(mods_path)
            if detected:
                ftb_quests_path = str(detected)
                self.ftb_quests_path.set(ftb_quests_path)

        try:
            config = self.build_config()
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror(
                "设置无效",
                "批量大小和并发请求数必须是正整数。",
            )
            return

        self.clear_log()
        self.progress_var.set(0)
        self.status_var.set("准备开始…")
        self._set_running_state(True)
        self.current_thread = threading.Thread(
            target=self.translation_worker,
            args=(
                mods_path,
                output_path,
                ftb_quests_path,
                bool(self.ftb_auto_import.get()),
                bool(self.skip_existing.get()),
                int(self.pack_format.get()),
                config,
            ),
            daemon=True,
        )
        self.current_thread.start()

    def stop_translation(self):
        if self.is_running:
            self.is_running = False
            self.status_var.set("正在停止，等待当前请求结束…")
            self.log("收到停止请求，正在安全退出…", "warning")

    def translation_worker(
        self,
        mods_path: str,
        output_path: str,
        ftb_quests_path: str,
        ftb_auto_import: bool,
        skip_existing: bool,
        pack_format: int,
        config: dict,
    ):
        try:
            self.log("=== 开始汉化流程 ===")

            scanner = None
            mod_infos = []
            if mods_path:
                self._queue_event("status", value="正在扫描模组…")
                scanner = ModScanner(mods_path)
                mod_infos = scanner.scan_all_mods()
            else:
                self.log("未选择 Mods 文件夹，跳过模组翻译。", "info")

            ftb_info = None
            ftb_entry_count = 0
            if ftb_quests_path:
                try:
                    self.log(f"正在扫描 FTB Quests：{ftb_quests_path}")
                    ftb_info = FTBQuestsScanner(ftb_quests_path).scan()
                    ftb_entry_count = ftb_info.translatable_count
                    for warning in ftb_info.warnings:
                        self.log(warning, "warning")
                    self.log(
                        f"FTB Quests 检测模式：{get_ftb_mode_label(ftb_info.mode)}",
                        "success",
                    )
                    self.log(
                        f"FTB Quests 可翻译条目：{ftb_entry_count} 条",
                        "success" if ftb_entry_count else "info",
                    )
                except Exception as error:
                    self.log(f"FTB Quests 扫描失败：{error}", "warning")
                    ftb_info = None
                    ftb_entry_count = 0
            else:
                self.log(
                    "未启用 FTB Quests 汉化或未指定任务书目录。",
                    "info",
                )

            if mod_infos:
                summary = scanner.get_summary(mod_infos)
                self.log(
                    f"模组扫描完成：共 {summary['total_mods']} 个命名空间",
                    "success",
                )
                self.log(
                    f"已有中文文件 {summary['has_zh_cn']} 个，"
                    f"完整汉化 {summary['fully_translated']} 个"
                )
                self.log(
                    f"部分汉化 {summary['partially_translated']} 个，"
                    f"缺少 {summary['partial_pending_translation_keys']} 条；"
                    f"完全无中文 {summary['without_zh_cn']} 个"
                )
                self.log(
                    f"总翻译条目 {summary['total_translation_keys']} 条，"
                    f"需要处理 {summary['need_translation']} 个命名空间"
                )
            elif mods_path:
                self.log("未找到包含 en_us.json 的模组。", "warning")

            if skip_existing:
                before_count = len(mod_infos)
                mod_infos = [
                    item for item in mod_infos
                    if not item.is_fully_translated
                ]
                skipped_count = before_count - len(mod_infos)
                if skipped_count:
                    self.log(
                        f"已跳过完整汉化的命名空间 {skipped_count} 个",
                        "info",
                    )
                if mod_infos:
                    pending_keys = sum(
                        item.pending_translation_count for item in mod_infos
                    )
                    self.log(
                        f"待处理命名空间 {len(mod_infos)} 个，"
                        f"预计翻译或补全 {pending_keys} 条文本",
                        "info",
                    )

            if not mod_infos and not ftb_entry_count:
                self.log("没有需要翻译的文本。", "warning")
                self._queue_event("status", value="无需翻译")
                return

            translator = TranslatorFactory.create_translator(config)
            translations_by_mod = {}
            quest_translations = {}
            total_jobs = int(bool(mod_infos)) + int(bool(ftb_entry_count))
            completed_jobs = 0

            total_mods = len(mod_infos)
            for index, mod_info in enumerate(mod_infos, 1):
                if not self.is_running:
                    raise TranslationCancelled("翻译已取消")

                source_content = (
                    mod_info.en_us_content
                    if not skip_existing
                    else mod_info.get_pending_en_us_content()
                )
                action = (
                    "重新翻译全部"
                    if not skip_existing
                    else ("补全缺失" if mod_info.has_zh_cn else "翻译")
                )
                self.log(
                    f"\n[{index}/{total_mods}] {action}："
                    f"{mod_info.mod_name} ({mod_info.mod_id})，"
                    f"{len(source_content)} 条"
                )

                def progress_callback(
                    current, total, detail, job_index=index - 1
                ):
                    ratio = current / total if total else 1
                    overall = (
                        (job_index + ratio) / total_jobs * 100
                    )
                    self._queue_event("progress", value=overall)
                    self._queue_event(
                        "status",
                        value=(
                            f"正在处理 {index}/{total_mods}：{detail}"
                        ),
                    )

                translations = translator.translate_dict(
                    source_content,
                    progress_callback=progress_callback,
                    cancel_check=lambda: not self.is_running,
                )
                translations_by_mod[mod_info.mod_id] = (
                    mod_info.merge_with_existing(translations)
                )
                self.log(f"处理完成：{mod_info.mod_name}", "success")
                completed_jobs += 1

            if ftb_info is not None and ftb_entry_count:
                if not self.is_running:
                    raise TranslationCancelled("翻译已取消")
                self.log(
                    f"\n正在翻译 FTB Quests 任务书：{ftb_entry_count} 条"
                )

                def quest_progress(current, total, detail):
                    ratio = current / total if total else 1
                    overall = (
                        (completed_jobs + ratio) / total_jobs * 100
                    )
                    self._queue_event("progress", value=overall)
                    self._queue_event(
                        "status",
                        value=f"正在翻译 FTB Quests：{detail}",
                    )

                quest_translations = translator.translate_dict(
                    ftb_info.translation_entries(),
                    progress_callback=quest_progress,
                    cancel_check=lambda: not self.is_running,
                )

            if not self.is_running:
                raise TranslationCancelled("翻译已取消")
            if not translations_by_mod and not quest_translations:
                self.log("没有成功完成任何翻译。", "error")
                self._queue_event("status", value="没有可打包的译文")
                return

            generated_any = False
            resourcepack_generated = False
            quest_folder_result = None

            ftb_resourcepack_files = {}
            if ftb_info is not None and quest_translations:
                ftb_resourcepack_files = build_resourcepack_files(
                    ftb_info, quest_translations
                )

            if translations_by_mod or ftb_resourcepack_files:
                self._queue_event("status", value="正在生成资源包…")
                self.log("\n正在生成资源包…")
                generator = ResourcePackGenerator(
                    pack_format=pack_format,
                    description="自动生成的中文汉化补丁",
                )
                output_path = generator.generate(
                    translations_by_mod,
                    output_path,
                    cancel_check=lambda: not self.is_running,
                    additional_files=ftb_resourcepack_files,
                )

                if generator.verify_resourcepack(output_path):
                    info = generator.get_resourcepack_info(output_path)
                    self.log("资源包验证通过", "success")
                    self.log(f"命名空间数量：{info['mod_count']}", "success")
                    self.log(
                        f"翻译条目：{info['total_translations']}", "success"
                    )
                    self.log(f"输出路径：{output_path}", "success")
                    resourcepack_generated = True
                    generated_any = True
                else:
                    self.log("资源包验证失败。", "error")

            if (
                ftb_info is not None
                and ftb_info.requires_quests_folder
                and quest_translations
            ):
                self._queue_event(
                    "status", value="正在生成完整 quests 汉化目录…"
                )
                self.log("\n正在生成完整 quests 汉化目录…")
                ftb_output = build_ftb_output_path(output_path)
                quest_folder_result = FTBQuestsFolderGenerator().generate(
                    ftb_info,
                    quest_translations,
                    ftb_output,
                    cancel_check=lambda: not self.is_running,
                )
                if quest_folder_result is not None:
                    self.log("quests 目录生成成功", "success")
                    self.log(
                        f"修改文件：{quest_folder_result.direct_count}",
                        "success",
                    )
                    self.log(
                        f"语言文件条目：{quest_folder_result.lang_count}",
                        "success",
                    )
                    self.log(
                        f"输出目录：{quest_folder_result.path}", "success"
                    )
                    generated_any = True

                    if ftb_auto_import:
                        self._queue_event(
                            "status", value="正在备份并替换 quests 目录…"
                        )
                        import_result = FTBQuestsImporter.import_quests(
                            quest_folder_result.path,
                            str(ftb_info.quests_dir),
                            cancel_check=lambda: not self.is_running,
                        )
                        self.log(
                            "已自动替换当前整合包的 quests 目录",
                            "success",
                        )
                        self.log(
                            f"原目录备份：{import_result.backup_path}",
                            "warning",
                        )
                    else:
                        self.log(
                            "未勾选一键导入：请手动备份原 quests 后，"
                            "用上述输出目录进行替换。",
                            "warning",
                        )
                else:
                    self.log(
                        "FTB Quests 没有产生可写入的新译文。", "warning"
                    )
            elif ftb_info is not None and ftb_info.mode == "localized_keys":
                self.log(
                    "FTB Quests 为本地化键模式，译文已合并到资源包，"
                    "无需替换 quests。",
                    "success",
                )
            if not generated_any:
                self.log("没有生成任何补丁。", "error")
                self._queue_event("status", value="生成失败")
                return

            self._queue_event("progress", value=100)
            self._queue_event("status", value="汉化完成")
        except TranslationCancelled:
            self.log("任务已停止，未生成不完整补丁。", "warning")
            self._queue_event("status", value="已停止")
        except Exception as error:
            self.log(f"发生错误：{error}", "error")
            import traceback

            self.log(traceback.format_exc(), "error")
            self._queue_event("status", value="运行失败")
        finally:
            self._queue_event("done")

    def build_config(self) -> dict:
        """从界面收集完整配置，供翻译器和配置保存共用。"""
        return {
            "api_type": self.api_type.get(),
            "api_configs": {
                "ai": {
                    "api_key": self.ai_api_key.get().strip(),
                    "base_url": self.ai_base_url.get().strip()
                    or "https://api.deepseek.com/v1",
                    "model": self.ai_model.get().strip() or "deepseek-chat",
                    "temperature": 0.3,
                    "max_tokens": 4000,
                },
                "baidu": {
                    "app_id": self.baidu_app_id.get().strip(),
                    "secret_key": self.baidu_secret_key.get().strip(),
                },
                "deepl": {
                    "auth_key": self.deepl_auth_key.get().strip(),
                },
            },
            "translation_settings": {
                "use_cache": bool(self.use_cache.get()),
                "skip_existing": bool(self.skip_existing.get()),
                "batch_size": max(1, int(self.batch_size.get())),
                "max_workers": max(1, int(self.max_workers.get())),
                "mc_terminology": {
                    "Crafting": "合成",
                    "Item": "物品",
                    "Block": "方块",
                    "Enchantment": "附魔",
                    "Potion": "药水",
                    "Effect": "效果",
                    "Inventory": "物品栏",
                    "Creative": "创造",
                    "Survival": "生存",
                },
            },
            "resourcepack_settings": {
                "pack_format": max(1, int(self.pack_format.get())),
                "description": "自动生成的中文汉化补丁",
                "mc_version": self.mc_version.get(),
            },
            "ftbquests_settings": {
                "quests_dir": self.ftb_quests_path.get().strip(),
                "auto_detect": bool(self.ftb_auto_detect.get()),
                "auto_import": bool(self.ftb_auto_import.get()),
            },
        }

    def save_config(self):
        """将界面配置保存到当前目录的 user_config.json。"""
        try:
            config_path = Path.cwd() / "user_config.json"
            content = json.dumps(
                self.build_config(), ensure_ascii=False, indent=2
            )
            config_path.write_text(content, encoding="utf-8")
        except (OSError, TypeError, ValueError, tk.TclError) as error:
            messagebox.showerror("保存失败", f"无法保存配置：\n{error}")
            return
        self.status_var.set(f"配置已保存：{config_path.name}")
        self.log(f"配置已保存到 {config_path}", "success")

    def load_config(self):
        """加载 user_config.json，不存在或字段缺失时保留安全默认值。"""
        config_path = Path.cwd() / "user_config.json"
        if not config_path.exists():
            return

        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置根节点必须是 JSON 对象")

            api_configs = data.get("api_configs", {})
            ai_config = api_configs.get("ai", {})
            baidu_config = api_configs.get("baidu", {})
            deepl_config = api_configs.get("deepl", {})
            settings = data.get("translation_settings", {})
            resource_settings = data.get("resourcepack_settings", {})
            ftb_settings = data.get("ftbquests_settings", {})

            self.api_type.set(data.get("api_type", "ai"))
            self.ai_api_key.set(ai_config.get("api_key", ""))
            self.ai_base_url.set(
                ai_config.get("base_url", "https://api.deepseek.com/v1")
            )
            self.ai_model.set(ai_config.get("model", "deepseek-chat"))
            self.baidu_app_id.set(baidu_config.get("app_id", ""))
            self.baidu_secret_key.set(baidu_config.get("secret_key", ""))
            self.deepl_auth_key.set(deepl_config.get("auth_key", ""))

            self.use_cache.set(bool(settings.get("use_cache", True)))
            self.skip_existing.set(bool(settings.get("skip_existing", True)))
            self.batch_size.set(max(1, int(settings.get("batch_size", 10))))
            self.max_workers.set(max(1, int(settings.get("max_workers", 3))))
            self.mc_version.set(
                resource_settings.get("mc_version", "1.20-1.20.2")
            )
            self.pack_format.set(
                max(1, int(resource_settings.get("pack_format", 15)))
            )
            self.ftb_quests_path.set(ftb_settings.get("quests_dir", ""))
            self.ftb_auto_detect.set(
                bool(ftb_settings.get("auto_detect", True))
            )
            self.ftb_auto_import.set(
                bool(ftb_settings.get("auto_import", False))
            )

            self.on_api_type_changed()
            self.on_mc_version_changed()
        except (OSError, ValueError, TypeError, tk.TclError) as error:
            messagebox.showwarning(
                "配置读取失败",
                f"已使用默认设置，配置文件可能已损坏：\n{error}",
            )

    def clear_cache(self):
        """清理全部翻译缓存。"""
        if not messagebox.askyesno(
            "清空缓存",
            "确定要清空全部翻译缓存吗？\n下次翻译时将重新调用翻译 API。",
        ):
            return
        try:
            TranslationCache.clear_all()
        except OSError as error:
            messagebox.showerror("清空失败", f"无法删除缓存：\n{error}")
            return
        self.status_var.set("翻译缓存已清空")
        self.log("翻译缓存已清空。", "success")

    def open_output_folder(self):
        """打开当前输出文件所在目录。"""
        output_path = self.output_path.get().strip()
        if not output_path:
            messagebox.showwarning("没有输出路径", "请先选择一个输出文件。")
            return
        folder = Path(output_path).expanduser().resolve().parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except (OSError, AttributeError) as error:
            messagebox.showerror("打开失败", f"无法打开输出目录：\n{error}")

    def on_closing(self):
        """安全关闭窗口；任务仍在运行时先征得用户确认。"""
        if self.is_running and not messagebox.askyesno(
            "任务正在运行",
            "翻译任务尚未完成，确定要退出吗？\n当前未完成的资源包不会生成。",
        ):
            return
        self._closing = True
        self.is_running = False
        poll_after_id = getattr(self, "_poll_after_id", None)
        if poll_after_id is not None:
            try:
                self.root.after_cancel(poll_after_id)
            except tk.TclError:
                pass
        self.root.destroy()


def run_gui():
    """创建并运行 GUI。"""
    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, ImportError, OSError):
        pass

    root = tk.Tk()
    TranslationApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
