"""Roblox Script Explorer: a native Windows UI for local Luau analysis."""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import tkinter as tk
import tkinter.font as tkfont
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from explorer_core import (Capture, Decompiler, ExplorerError, Script, TOKEN, analyze,
                           capture_live, export_capture, load_capture,
                           resolve_dependencies, write_json)
from explorer_theme import MODES, THEMES, resolve, set_title_bar

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
CODE_TAGS = ('keyword', 'string', 'number', 'comment', 'function')


class CodeView(tk.Frame):
    def __init__(self, master, app):
        super().__init__(master, highlightthickness=app.px(2))
        self.app = app
        self.gutter = tk.Canvas(self, width=app.px(60), highlightthickness=0)
        self.gutter.pack(side='left', fill='y')
        self.text = tk.Text(self, wrap='none', relief='flat', borderwidth=0, highlightthickness=0,
                            font=app.fonts['code'], padx=app.px(12), pady=app.px(14), undo=False,
                            state='disabled', spacing1=1, spacing3=1)
        # Four-space tab stops instead of 4 cm ones.
        self.text.configure(tabs=(tkfont.Font(self, font=app.fonts['code']).measure('    '),))
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        self.text.pack(fill='both', expand=True)
        self.text.configure(yscrollcommand=lambda a, b: (vertical.set(a, b), self.redraw()),
                            xscrollcommand=horizontal.set)
        self.text.bind('<Configure>', lambda e: self.redraw())
        self._pending = False
        app.focus_ring(self, self.text, 'code_bg')
        app.on_theme(self.apply_theme)

    def apply_theme(self, t):
        self.configure(bg=t['code_bg'])
        self.gutter.configure(bg=t['code_bg'])
        self.text.configure(bg=t['code_bg'], fg=t['text'], insertbackground=t['text'],
                            selectbackground=t['selection'], selectforeground=t['on_selection'],
                            inactiveselectbackground=t['selection'])
        for tag in CODE_TAGS:
            self.text.tag_configure(tag, foreground=t[tag])
        self.text.tag_configure('match', background=t['match'], foreground=t['on_match'])
        self.text.tag_configure('active_match', background=t['active_match'], foreground=t['on_active_match'])
        self.redraw()

    def redraw(self):
        if getattr(self, '_pending', False):
            return
        self._pending = True
        self.after_idle(self._draw)

    def _draw(self):
        self._pending = False
        self.gutter.delete('all')
        index = self.text.index('@0,0')
        for _ in range(150):
            box = self.text.dlineinfo(index)
            if box is None:
                break
            line = index.split('.')[0]
            self.gutter.create_text(self.app.px(46), box[1] + 1, text=line, anchor='ne',
                                    fill=self.app.palette['gutter'], font=self.app.fonts['gutter'])
            index = self.text.index(f'{index}+1line')

    def show(self, content: str, highlight=True):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        # Large modules remain exportable; keep the editor responsive.
        displayed = content[:2_000_000]
        if len(displayed) < len(content):
            displayed += '\n\n-- Affichage limité à 2 millions de caractères. Exportez le fichier complet.'
        self.text.insert('1.0', displayed)
        if highlight and len(displayed) < 500_000:
            keywords = set('and break continue do else elseif end export false for function if in local nil not or repeat return then true type until while'.split())
            for match in TOKEN.finditer(displayed):
                value = match.group()
                tag = ('comment' if value.startswith('--') else
                       'string' if value[:1] in ('"', "'") or re.match(r'^\[=*\[', value) else
                       'keyword' if value in keywords else 'number' if value[:1].isdigit() else None)
                if tag:
                    self.text.tag_add(tag, f'1.0+{match.start()}c', f'1.0+{match.end()}c')
        self.text.configure(state='disabled')
        self.text.yview_moveto(0)
        self.redraw()

    def go(self, line):
        self.text.see(f'{line}.0')
        self.text.mark_set('insert', f'{line}.0')
        self.redraw()


class ThemeSwitch(tk.Frame):
    """Light/dark switch, usable with the mouse, Tab and Space/Enter."""

    def __init__(self, master, app):
        super().__init__(master, takefocus=1, cursor='hand2', highlightthickness=app.px(2),
                         padx=app.px(12), pady=app.px(8))
        self.app = app
        self.hovered = False
        self.label = tk.Label(self, text='Thème sombre', font=app.fonts['body'], cursor='hand2')
        self.label.pack(side='left', padx=(0, app.px(10)))
        self.track = tk.Canvas(self, width=app.px(44), height=app.px(24), highlightthickness=0, cursor='hand2')
        self.track.pack(side='left')
        for widget in (self, self.label, self.track):
            widget.bind('<Button-1>', self.toggle)
            widget.bind('<Enter>', lambda e: self._hover(True))
            widget.bind('<Leave>', lambda e: self._hover(False))
        self.bind('<space>', self.toggle)
        self.bind('<Return>', self.toggle)
        app.focus_ring(self, self, 'bg')
        app.on_theme(self.apply_theme)

    def toggle(self, _=None):
        self.app.set_theme('light' if self.app.theme == 'dark' else 'dark')
        return 'break'

    def _hover(self, value):
        self.hovered = value
        self.apply_theme(self.app.palette)

    def apply_theme(self, t):
        background = t['hover'] if self.hovered else t['bg']
        on = self.app.theme == 'dark'
        self.configure(bg=background)
        self.label.configure(bg=background, fg=t['text'])
        self.track.configure(bg=background)
        self.track.delete('all')
        w, h, px = self.app.px(44), self.app.px(24), self.app.px
        fill = t['accent'] if on else t['muted']
        self.track.create_oval(0, 0, h, h, fill=fill, outline=fill)
        self.track.create_oval(w - h, 0, w, h, fill=fill, outline=fill)
        self.track.create_rectangle(h // 2, 0, w - h // 2, h, fill=fill, outline=fill)
        x = w - h // 2 if on else h // 2
        knob = t['on_accent'] if on else t['surface']
        self.track.create_oval(x - px(8), h // 2 - px(8), x + px(8), h // 2 + px(8), fill=knob, outline=knob)


class Explorer(tk.Tk):
    def __init__(self, capture_path=None, engine_path=None):
        super().__init__()
        self.title('Roblox Script Explorer')
        self.local = ROOT / '.local'
        self.local.mkdir(exist_ok=True)
        self.capture: Capture | None = None
        self.engine: Decompiler | None = None
        self.selected: Script | None = None
        self.records = {}
        self.generation = 0
        self.events = queue.Queue()
        self.workers = ThreadPoolExecutor(max_workers=3, thread_name_prefix='rse')
        self.cancel = threading.Event()
        self.batch_running = False
        self.encoding = tk.StringVar(value='roblox')
        self.filter = tk.StringVar()
        self.kind_filter = tk.StringVar(value='Tous')
        self.status = tk.StringVar(value='Ouvrez une capture pour commencer.')
        self.settings = self._read_settings()
        # Pixel sizes are written for 96 DPI and scaled to the screen.
        self.scale = self.winfo_fpixels('1i') / 96
        self.geometry(f'{self.px(1440)}x{self.px(920)}')
        self.minsize(self.px(1060), self.px(700))
        self._fonts()
        self.theme_mode = tk.StringVar(value=self.settings.get('theme') if self.settings.get('theme') in MODES else 'system')
        self.theme = resolve(self.theme_mode.get())
        self.palette = THEMES[self.theme]
        self._theme_hooks = []
        self._combos = []
        self.style = ttk.Style(self)
        self.style.theme_use('clam')
        self._style(self.palette)
        self._build()
        self.apply_theme()
        self._configure_engine(engine_path or self.settings.get('engine') or ROOT / 'tools' / 'luaudec.exe', quiet=True)
        self.after(100, self._poll)
        self.after(2000, self._follow_system_theme)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('<Control-o>', lambda e: self.open_folder())
        self.bind('<Control-f>', lambda e: self.focus_find())
        self.bind('<Control-g>', lambda e: self.go_line())
        self.bind('<Control-s>', lambda e: self.export_source())
        if capture_path:
            self.after(150, lambda: self.open_path(Path(capture_path)))
        elif self.settings.get('capture') and Path(self.settings['capture']).exists():
            self.after(150, lambda: self.open_path(Path(self.settings['capture'])))

    def _read_settings(self):
        try:
            return json.loads((self.local / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return {}

    def px(self, value):
        return max(1, round(value * self.scale))

    def _fonts(self):
        families = set(tkfont.families(self))
        mono = next((name for name in ('Cascadia Mono', 'Consolas') if name in families), 'Courier New')
        ui, strong = 'Segoe UI', 'Segoe UI Semibold'
        self.fonts = {'title': (strong, 17), 'heading': (strong, 14), 'stat': (strong, 20),
                      'body': (ui, 10), 'small': (ui, 9), 'label': (strong, 9), 'button': (strong, 10),
                      'brand': ('Segoe UI Black', 13), 'code': (mono, 11), 'gutter': (mono, 10)}

    # Theme plumbing: widgets register a hook that receives the active palette.
    def on_theme(self, hook):
        self._theme_hooks.append(hook)
        hook(self.palette)
        return hook

    def skin(self, widget, **roles):
        """Bind widget options to palette tokens, e.g. skin(label, bg='surface', fg='muted')."""
        self.on_theme(lambda t: widget.configure(**{option: t[token] for option, token in roles.items()}))
        return widget

    def focus_ring(self, holder, widget, rest):
        """Draw a focus-coloured ring on `holder` while `widget` has keyboard focus."""
        def paint(t=None):
            t = t or self.palette
            focused = self.focus_get() is widget
            holder.configure(highlightbackground=t['focus'] if focused else t[rest],
                             highlightcolor=t['focus'])
        widget.bind('<FocusIn>', lambda e: paint(), add='+')
        widget.bind('<FocusOut>', lambda e: paint(), add='+')
        self.on_theme(paint)

    def hover_rows(self, tree):
        state = {'row': ''}
        def mark(row, on):
            if row and tree.exists(row):
                tags = [tag for tag in tree.item(row, 'tags') or () if tag != 'hover']
                tree.item(row, tags=tags + ['hover'] if on else tags)
        def move(event):
            row = tree.identify_row(event.y)
            if row in tree.selection():
                row = ''
            if row != state['row']:
                mark(state['row'], False)
                mark(row, True)
                state['row'] = row
        def leave(_):
            mark(state['row'], False)
            state['row'] = ''
        tree.bind('<Motion>', move, add='+')
        tree.bind('<Leave>', leave, add='+')
        tree.bind('<<TreeviewSelect>>', leave, add='+')
        self.on_theme(lambda t: (tree.tag_configure('hover', background=t['hover']),
                                 tree.tag_configure('Partiel', foreground=t['warning']),
                                 tree.tag_configure('Échec', foreground=t['danger'])))

    def set_theme(self, mode):
        self.theme_mode.set(mode)
        self.settings['theme'] = mode
        self.save_settings()
        self.apply_theme()

    def apply_theme(self):
        self.theme = resolve(self.theme_mode.get())
        self.palette = t = THEMES[self.theme]
        self.configure(bg=t['bg'])
        self._style(t)
        alive = []
        for hook in self._theme_hooks:
            try:
                hook(t)
                alive.append(hook)
            except tk.TclError:  # the widget was destroyed, e.g. a closed search window
                pass
        self._theme_hooks = alive
        set_title_bar(self, self.theme == 'dark')

    def _follow_system_theme(self):
        if self.theme_mode.get() == 'system' and resolve('system') != self.theme:
            self.apply_theme()
        self.after(2000, self._follow_system_theme)

    def _style(self, t):
        style, px = self.style, self.px
        flat = lambda color: dict(background=color, lightcolor=color, darkcolor=color)
        style.configure('.', font=self.fonts['body'], foreground=t['text'], troughcolor=t['surface'],
                        bordercolor=t['border'], focuscolor=t['focus'], selectbackground=t['selection'],
                        selectforeground=t['on_selection'], insertcolor=t['text'], fieldbackground=t['bg'],
                        **flat(t['surface']))
        style.configure('TFrame', background=t['surface'])
        style.configure('TLabel', background=t['surface'], foreground=t['text'])
        style.configure('TButton', foreground=t['text'], bordercolor=t['border'], focusthickness=2,
                        padding=(px(16), px(11)), font=self.fonts['button'], anchor='center', **flat(t['raised']))
        states = lambda base, hover, pressed: [('disabled', base), ('pressed', pressed), ('active', hover)]
        for option in ('background', 'lightcolor', 'darkcolor'):
            style.map('TButton', **{option: states(t['raised'], t['hover'], t['pressed'])})
            style.map('Accent.TButton', **{option: states(t['accent'], t['accent_hover'], t['accent_hover'])})
        style.map('TButton', foreground=[('disabled', t['disabled'])], bordercolor=[('focus', t['focus'])])
        style.configure('Accent.TButton', foreground=t['on_accent'], bordercolor=t['accent'], **flat(t['accent']))
        style.map('Accent.TButton', foreground=[('disabled', t['on_accent'])], bordercolor=[('focus', t['focus'])])
        field = dict(fieldbackground=t['bg'], foreground=t['text'], insertcolor=t['text'],
                     bordercolor=t['border'], lightcolor=t['bg'], darkcolor=t['bg'])
        style.configure('TEntry', padding=(px(10), px(12)), **field)
        style.map('TEntry', bordercolor=[('focus', t['focus'])], lightcolor=[('focus', t['focus'])])
        style.configure('TCombobox', padding=(px(10), px(12)), background=t['raised'], arrowcolor=t['text'],
                        arrowsize=px(14), **field)
        style.map('TCombobox', fieldbackground=[('readonly', t['bg'])], foreground=[('readonly', t['text'])],
                  background=[('active', t['hover'])], bordercolor=[('focus', t['focus'])],
                  lightcolor=[('focus', t['focus'])], arrowcolor=[('disabled', t['disabled'])],
                  selectbackground=[('readonly', t['bg'])], selectforeground=[('readonly', t['text'])])
        style.configure('Treeview', foreground=t['text'], fieldbackground=t['surface'], borderwidth=0,
                        rowheight=px(36), font=self.fonts['body'], bordercolor=t['surface'], **flat(t['surface']))
        style.map('Treeview', background=[('selected', t['selection'])], foreground=[('selected', t['on_selection'])])
        style.configure('Treeview.Heading', foreground=t['muted'], relief='flat', padding=(px(10), px(9)),
                        font=self.fonts['label'], bordercolor=t['border'], **flat(t['raised']))
        style.map('Treeview.Heading', background=[('active', t['hover'])], foreground=[('active', t['text'])])
        style.configure('TNotebook', borderwidth=0, bordercolor=t['surface'], tabmargins=(px(8), px(8), px(8), 0),
                        **flat(t['surface']))
        style.configure('TNotebook.Tab', foreground=t['muted'], padding=(px(18), px(12)), font=self.fonts['button'],
                        bordercolor=t['surface'], **flat(t['surface']))
        style.map('TNotebook.Tab', padding=[('selected', (px(18), px(12)))], expand=[('selected', (0, 0, 0, 0))],
                  foreground=[('selected', t['accent_text']), ('active', t['text'])],
                  background=[('selected', t['raised']), ('active', t['hover'])],
                  lightcolor=[('selected', t['raised']), ('active', t['hover'])],
                  bordercolor=[('selected', t['border'])])
        style.configure('TScrollbar', troughcolor=t['surface'], bordercolor=t['surface'], arrowcolor=t['muted'],
                        gripcount=0, arrowsize=px(14), **flat(t['raised']))
        style.map('TScrollbar', background=[('active', t['hover'])], lightcolor=[('active', t['hover'])],
                  darkcolor=[('active', t['hover'])])
        style.configure('Horizontal.TProgressbar', troughcolor=t['raised'], bordercolor=t['raised'],
                        thickness=px(8), **flat(t['success']))
        # Classic Tk dialogs (simpledialog) and combobox lists read the option database.
        for pattern, token in [('*background', 'surface'), ('*foreground', 'text'), ('*activeBackground', 'hover'),
                               ('*activeForeground', 'text'), ('*selectBackground', 'selection'),
                               ('*selectForeground', 'on_selection'), ('*insertBackground', 'text'),
                               ('*highlightBackground', 'surface'), ('*highlightColor', 'focus')]:
            self.option_add(pattern, t[token])
        self.option_add('*TCombobox*Listbox.font', self.fonts['body'])
        for combo in self._combos:
            popdown = self.tk.call('ttk::combobox::PopdownWindow', combo)
            self.tk.call(f'{popdown}.f.l', 'configure', '-background', t['surface'], '-foreground', t['text'],
                         '-selectbackground', t['selection'], '-selectforeground', t['on_selection'])

    def _combo(self, parent, **kwargs):
        combo = ttk.Combobox(parent, state='readonly', font=self.fonts['body'], **kwargs)
        self._combos.append(combo)
        return combo

    def _build(self):
        px, skin = self.px, self.skin
        menu = tk.Menu(self, tearoff=False)
        filemenu = tk.Menu(menu, tearoff=False)
        for label, action in [('Ouvrir une capture…', self.open_folder), ('Ouvrir un manifeste ou bytecode…', self.open_file),
                              ('Capturer Roblox', self.live_capture), ('Exporter le script…', self.export_source),
                              ('Exporter le projet analysé…', self.export_project), ('Quitter', self.close)]:
            filemenu.add_command(label=label, command=action)
        menu.add_cascade(label='Fichier', menu=filemenu)
        engine_menu = tk.Menu(menu, tearoff=False)
        engine_menu.add_command(label='Choisir luaudec.exe…', command=self.choose_engine)
        engine_menu.add_command(label='Instructions de construction', command=self.backend_help)
        menu.add_cascade(label='Moteur', menu=engine_menu)
        view_menu = tk.Menu(menu, tearoff=False)
        for label, mode in [('Suivre le thème de Windows', 'system'), ('Thème clair', 'light'), ('Thème sombre', 'dark')]:
            view_menu.add_radiobutton(label=label, value=mode, variable=self.theme_mode,
                                      command=lambda mode=mode: self.set_theme(mode))
        menu.add_cascade(label='Affichage', menu=view_menu)
        self.configure(menu=menu)

        # Header: brand, engine state and theme switch.
        top = skin(tk.Frame(self, padx=px(24), pady=px(16)), bg='bg')
        top.pack(fill='x')
        skin(tk.Label(top, text='RSE', padx=px(10), pady=px(8), font=self.fonts['brand']),
             bg='accent', fg='on_accent').pack(side='left', padx=(0, px(14)))
        brand = skin(tk.Frame(top), bg='bg')
        brand.pack(side='left')
        skin(tk.Label(brand, text='Roblox Script Explorer', font=self.fonts['title']), bg='bg', fg='text').pack(anchor='w')
        skin(tk.Label(brand, text='Capturer, comprendre et explorer les scripts Luau localement',
                      font=self.fonts['small']), bg='bg', fg='muted').pack(anchor='w')
        ThemeSwitch(top, self).pack(side='right')
        self.engine_label = tk.Label(top, font=self.fonts['label'], padx=px(12), pady=px(6))
        self.engine_label.pack(side='right', padx=px(16))
        self.on_theme(lambda t: self._paint_engine())

        # Toolbar: one primary action, secondary actions, then mode and stop.
        toolbar = skin(tk.Frame(self, padx=px(12), pady=px(10), highlightthickness=1),
                       bg='surface', highlightbackground='border', highlightcolor='border')
        toolbar.pack(fill='x', padx=px(24))
        for label, action, accent in [('Ouvrir capture', self.open_folder, True), ('Capturer Roblox', self.live_capture, False),
                                      ('Décompiler tout', self.decompile_all, False), ('Exporter', self.export_project, False)]:
            ttk.Button(toolbar, text=label, command=action, style='Accent.TButton' if accent else 'TButton').pack(side='left', padx=px(4))
        ttk.Button(toolbar, text='Arrêter', command=self.stop).pack(side='right', padx=px(4))
        self.encoding_combo = self._combo(toolbar, textvariable=self.encoding, values=('roblox', 'standard', 'auto'), width=10)
        self.encoding_combo.pack(side='right', padx=(px(4), px(12)))
        skin(tk.Label(toolbar, text='Mode', font=self.fonts['small']), bg='surface', fg='muted').pack(side='right')
        self.encoding.trace_add('write', self.encoding_changed)

        # Summary cards.
        cards = skin(tk.Frame(self, padx=px(20), pady=px(14)), bg='bg')
        cards.pack(fill='x')
        self.count_labels = []
        for title in ('Scripts', 'Contenus distincts', 'Code reconstruit', 'À examiner'):
            cell = skin(tk.Frame(cards, padx=px(16), pady=px(10), highlightthickness=1),
                        bg='surface', highlightbackground='border', highlightcolor='border')
            cell.pack(side='left', fill='x', expand=True, padx=px(4))
            label = skin(tk.Label(cell, text='—', font=self.fonts['stat']), bg='surface', fg='text')
            label.pack(anchor='w')
            skin(tk.Label(cell, text=title, font=self.fonts['small']), bg='surface', fg='muted').pack(anchor='w')
            self.count_labels.append(label)

        self.panes = skin(tk.PanedWindow(self, orient='horizontal', sashwidth=px(12), borderwidth=0, sashcursor='sb_h_double_arrow'),
                          bg='bg')
        self.panes.pack(fill='both', expand=True, padx=px(24))

        # Left column: filters and script tree.
        left = skin(tk.Frame(self.panes, highlightthickness=1), bg='surface', highlightbackground='border', highlightcolor='border')
        self.panes.add(left, minsize=px(280), width=px(370))
        heading = skin(tk.Frame(left, padx=px(14), pady=px(12)), bg='surface')
        heading.pack(fill='x')
        skin(tk.Label(heading, text='Explorateur', font=self.fonts['heading']), bg='surface', fg='text').pack(anchor='w')
        self.capture_label = skin(tk.Label(heading, text='Aucune capture ouverte', anchor='w', font=self.fonts['small']),
                                  bg='surface', fg='muted')
        self.capture_label.pack(fill='x')
        searchrow = skin(tk.Frame(left, padx=px(14)), bg='surface')
        searchrow.pack(fill='x')
        skin(tk.Label(searchrow, text='Filtrer par nom ou chemin', font=self.fonts['small']),
             bg='surface', fg='muted').pack(anchor='w', pady=(0, px(4)))
        entry = ttk.Entry(searchrow, textvariable=self.filter, font=self.fonts['body'])
        entry.pack(fill='x', pady=(0, px(10)))
        skin(tk.Label(searchrow, text='Classe ou état', font=self.fonts['small']),
             bg='surface', fg='muted').pack(anchor='w', pady=(0, px(4)))
        self._combo(searchrow, textvariable=self.kind_filter,
                    values=('Tous', 'LocalScript', 'ModuleScript', 'Reconstruit', 'Partiel', 'Échec')).pack(fill='x', pady=(0, px(10)))
        ttk.Button(searchrow, text='Rechercher dans le code…', command=self.global_search).pack(fill='x', pady=(0, px(12)))
        self.filter.trace_add('write', self.filter_changed)
        self.kind_filter.trace_add('write', self.filter_changed)
        treeframe = tk.Frame(left, highlightthickness=px(2))
        treeframe.pack(fill='both', expand=True, padx=px(4), pady=(0, px(4)))
        scroll = ttk.Scrollbar(treeframe)
        scroll.pack(side='right', fill='y')
        self.tree = ttk.Treeview(treeframe, columns=('status',), show='tree headings', yscrollcommand=scroll.set,
                                 selectmode='browse')
        self.tree.heading('#0', text='Scripts', anchor='w')
        self.tree.heading('status', text='État', anchor='w')
        self.tree.column('#0', width=px(220), minwidth=px(100))
        self.tree.column('status', width=px(110), minwidth=px(90), stretch=False)
        self.tree.pack(fill='both', expand=True)
        scroll.configure(command=self.tree.yview)
        self.tree.bind('<<TreeviewSelect>>', self.select_script)
        self.hover_rows(self.tree)
        self.focus_ring(treeframe, self.tree, 'surface')

        # Right column: script header and tabs.
        right = skin(tk.Frame(self.panes), bg='bg')
        self.panes.add(right, minsize=px(620))
        header = skin(tk.Frame(right, padx=px(18), pady=px(12), highlightthickness=1),
                      bg='surface', highlightbackground='border', highlightcolor='border')
        header.pack(fill='x', pady=(0, px(10)))
        self.script_label = skin(tk.Label(header, text='Votre espace d’analyse', font=self.fonts['heading'], anchor='w'),
                                 bg='surface', fg='text')
        self.script_label.pack(fill='x')
        self.path_label = skin(tk.Label(header, text='Ouvrez un dossier de capture ou un fichier .luac.',
                                        anchor='w', justify='left', font=self.fonts['small'], wraplength=px(850)),
                               bg='surface', fg='muted')
        self.path_label.pack(fill='x', pady=(px(2), 0))
        body = skin(tk.Frame(right, highlightthickness=1), bg='surface', highlightbackground='border', highlightcolor='border')
        body.pack(fill='both', expand=True)
        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill='both', expand=True)
        code_tab = ttk.Frame(self.notebook)
        self.notebook.add(code_tab, text='Code Luau')
        findbar = skin(tk.Frame(code_tab, padx=px(10), pady=px(8)), bg='surface')
        findbar.pack(fill='x')
        self.find_text = tk.StringVar()
        self.find_entry = ttk.Entry(findbar, textvariable=self.find_text, width=28, font=self.fonts['body'])
        self.find_entry.pack(side='left', padx=(0, px(8)))
        self.find_entry.bind('<Return>', lambda e: self.find_next())
        ttk.Button(findbar, text='Suivant', command=self.find_next).pack(side='left')
        ttk.Button(findbar, text='Aller à la ligne', command=self.go_line).pack(side='left', padx=px(8))
        ttk.Button(findbar, text='Exporter .luau', command=self.export_source).pack(side='right')
        self.code = CodeView(code_tab, self)
        self.code.pack(fill='both', expand=True)
        self.code.show('-- Bienvenue dans Roblox Script Explorer.\n\n-- Ouvrez une capture pour explorer son arborescence.\n-- Choisissez un script pour reconstruire son code localement.\n-- Aucun script récupéré n’est exécuté.', True)

        analysis_tab = skin(tk.Frame(self.notebook), bg='surface')
        self.notebook.add(analysis_tab, text='Analyse')
        # The dependency table is packed first, from the bottom, so the text never squeezes it out.
        self.dependencies = ttk.Treeview(analysis_tab, columns=('expression', 'resolution'), show='headings', height=4)
        self.dependencies.heading('expression', text='require(...)', anchor='w')
        self.dependencies.heading('resolution', text='Résolution statique', anchor='w')
        self.dependencies.column('expression', width=px(450))
        self.dependencies.column('resolution', width=px(190))
        self.dependencies.pack(side='bottom', fill='x', padx=px(14), pady=(0, px(14)))
        skin(tk.Label(analysis_tab, text='Dépendances inférées · double-cliquez pour ouvrir un chemin résolu',
                      anchor='w', padx=px(16), pady=px(8), font=self.fonts['label']),
             bg='surface', fg='muted').pack(side='bottom', fill='x')
        self.analysis_text = self._text(analysis_tab, font=self.fonts['body'], height=6, spacing1=2, spacing3=2)
        self.analysis_text.pack(fill='both', expand=True, padx=px(14), pady=px(12))
        self.on_theme(lambda t: self.analysis_text.tag_configure('heading', foreground=t['accent_text'],
                                                                 font=self.fonts['label'], spacing1=px(6)))
        self.dependencies.bind('<Double-1>', self.open_dependency)
        self.hover_rows(self.dependencies)
        self.dep_targets = {}

        constants_tab = ttk.Frame(self.notebook)
        self.notebook.add(constants_tab, text='Constantes')
        self.constants = ttk.Treeview(constants_tab, columns=('function', 'kind', 'value'), show='headings')
        for key, title, width in [('function', 'Fonction', 180), ('kind', 'Type', 100), ('value', 'Valeur', 650)]:
            self.constants.heading(key, text=title, anchor='w')
            self.constants.column(key, width=px(width), stretch=key == 'value')
        const_scroll = ttk.Scrollbar(constants_tab, command=self.constants.yview)
        const_scroll.pack(side='right', fill='y')
        self.constants.configure(yscrollcommand=const_scroll.set)
        self.constants.pack(fill='both', expand=True)
        self.hover_rows(self.constants)

        disasm_tab = ttk.Frame(self.notebook)
        self.notebook.add(disasm_tab, text='Désassemblage')
        self.disasm = CodeView(disasm_tab, self)
        self.disasm.pack(fill='both', expand=True)
        meta_tab = ttk.Frame(self.notebook)
        self.notebook.add(meta_tab, text='Métadonnées')
        self.meta = self._text(meta_tab, font=self.fonts['gutter'], bg_token='code_bg')
        self.meta.pack(fill='both', expand=True, padx=px(12), pady=px(12))
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)

        bottom = skin(tk.Frame(self, padx=px(24), pady=px(10)), bg='bg')
        bottom.pack(side='bottom', before=self.panes, fill='x')
        self.progress = ttk.Progressbar(bottom, mode='determinate', length=px(220))
        self.progress.pack(side='right', padx=(px(10), 0))
        skin(tk.Label(bottom, textvariable=self.status, anchor='w', font=self.fonts['body']),
             bg='bg', fg='muted').pack(side='left', fill='x', expand=True)
        self._filter_after = None

    def _text(self, parent, bg_token='surface', **kwargs):
        widget = tk.Text(parent, relief='flat', wrap='word', padx=self.px(8), pady=self.px(8),
                         highlightthickness=self.px(2), state='disabled', **kwargs)
        self.skin(widget, bg=bg_token, fg='text', insertbackground='text', selectbackground='selection',
                  selectforeground='on_selection', inactiveselectbackground='selection')
        self.focus_ring(widget, widget, bg_token)
        return widget

    def _paint_engine(self):
        t = self.palette
        ready = self.engine is not None
        self.engine_label.configure(text='●  Moteur local prêt' if ready else '●  Moteur à configurer',
                                    bg=t['surface'], fg=t['success'] if ready else t['warning'])

    def text_set(self, widget, content):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.configure(state='disabled')

    def _configure_engine(self, path, quiet=False):
        try:
            self.engine = Decompiler(Path(path), self.local / 'cache', ROOT / 'tools' / 'luau-compile.exe')
            self.settings['engine'] = str(self.engine.executable)
            self._paint_engine()
            self.save_settings()
            return True
        except (ExplorerError, OSError) as exc:
            self.engine = None
            self._paint_engine()
            if not quiet:
                messagebox.showerror('Décompilateur', str(exc), parent=self)
            return False

    def need_engine(self):
        if self.engine:
            return True
        self.backend_help()
        return False

    def backend_help(self):
        messagebox.showinfo('Configurer le moteur local',
                            'Depuis le dossier du projet, lancez dans PowerShell :\n\n'
                            '.\\Build-Backend.ps1 -WithCompiler\n\n'
                            'Prérequis : Git, CMake et les outils C++ de Visual Studio.\n'
                            'Vous pouvez aussi choisir un luaudec.exe déjà compilé dans le menu Moteur.', parent=self)

    def choose_engine(self):
        if self.batch_running:
            self.status.set('Arrêtez le lot avant de changer de moteur.')
            return
        selected = filedialog.askopenfilename(title='Choisir luaudec.exe', filetypes=[('Exécutable', '*.exe')], parent=self)
        if selected and self._configure_engine(selected):
            if self.capture:
                for item in self.capture.scripts:
                    item.source, item.status, item.analysis, item.structural = '', 'À décompiler', {}, {}
                self.rebuild_tree()

    def save_settings(self):
        write_json(self.local / 'settings.json', self.settings)

    def open_folder(self):
        chosen = filedialog.askdirectory(title='Dossier contenant le manifeste de capture', parent=self)
        if chosen:
            self.open_path(Path(chosen))

    def open_file(self):
        chosen = filedialog.askopenfilename(title='Ouvrir un manifeste ou du bytecode',
                                           filetypes=[('Capture et bytecode', '*.json *.luac *.luauc'), ('Tous', '*.*')], parent=self)
        if chosen:
            self.open_path(Path(chosen))

    def open_path(self, path):
        if self.batch_running:
            messagebox.showinfo('Traitement en cours', 'Arrêtez le lot avant de changer de capture.', parent=self)
            return
        self.generation += 1
        self.selected = None
        self.code.show('-- Chargement de la capture…')
        self.status.set('Lecture et vérification des empreintes…')
        engine, encoding = self.engine, self.encoding.get()
        def load():
            capture = load_capture(path)
            if engine:
                for item in capture.scripts:
                    engine.restore_cached(item, encoding)
            return capture
        self._task(load, lambda capture: self.install_capture(capture, path))

    def install_capture(self, capture, selected_path):
        self.capture = capture
        self.records = {item.id: item for item in capture.scripts}
        self.settings['capture'] = str(selected_path)
        self.save_settings()
        self.capture_label.configure(text='Capture · ' + capture.build.replace('version-', '')[:22])
        self.filter.set('')
        self.rebuild_tree()
        self.update_counts()
        warnings = f' · {len(capture.warnings)} avertissement(s)' if capture.warnings else ''
        self.status.set(f'{len(capture.scripts)} scripts ouverts{warnings}. Analyse locale, aucun code exécuté.')
        if capture.scripts:
            self.focus_script(capture.scripts[0].id)

    def filter_changed(self, *_):
        if getattr(self, '_filter_after', None):
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(200, self.rebuild_tree)

    def rebuild_tree(self):
        self._filter_after = None
        if not self.capture:
            return
        selected = self.selected.id if self.selected else None
        self.tree.delete(*self.tree.get_children())
        folders = {}
        query, kind = self.filter.get().casefold(), self.kind_filter.get()
        for script in self.capture.scripts:
            if query and query not in (script.name + ' ' + script.path).casefold():
                continue
            if kind != 'Tous' and kind not in (script.kind, script.status):
                continue
            parent, prefix = '', ''
            for part in script.path.split('/')[:-1]:
                prefix += '/' + part
                if prefix not in folders:
                    folder = 'f' + str(len(folders))
                    self.tree.insert(parent, 'end', iid=folder, text=part, open=bool(query) or prefix.count('/') <= 2)
                    folders[prefix] = folder
                parent = folders[prefix]
            self.tree.insert(parent, 'end', iid=script.id, text=script.name, values=(script.status,), tags=(script.status,))
        if selected and self.tree.exists(selected):
            self.tree.selection_set(selected)

    def focus_script(self, ident, line=None):
        if not self.tree.exists(ident):
            self.filter.set('')
            self.kind_filter.set('Tous')
            self.rebuild_tree()
        if self.tree.exists(ident):
            self.tree.see(ident)
            self.tree.selection_set(ident)
            self.tree.focus(ident)
        if line:
            self.after(200, lambda: self.code.go(line))

    def select_script(self, _=None):
        selection = self.tree.selection()
        if not selection or selection[0] not in self.records:
            return
        item = self.records[selection[0]]
        self.selected = item
        self.script_label.configure(text=item.name)
        self.path_label.configure(text=item.path)
        if item.source:
            self.display_script(item)
        else:
            self.code.show('-- Décompilation locale en cours…')
            if self.need_engine():
                engine, encoding = self.engine, self.encoding.get()
                self._task(lambda: engine.decompile(item, encoding), self.script_done)

    def script_done(self, item):
        self.update_row(item)
        self.update_counts()
        if self.selected and self.selected.id == item.id:
            self.display_script(item)

    def display_script(self, item):
        self.code.show(item.source or '-- ' + '\n-- '.join(item.diagnostics))
        self.render_analysis(item)
        metadata = {'nom': item.name, 'chemin': item.path, 'classe': item.kind, 'statut': item.status,
                    'syntaxe': item.syntax, 'bytecode_version': item.version, 'octets': item.size,
                    'sha256': item.digest, 'fichier_local': str(item.file),
                    'diagnostics': item.diagnostics, 'capture': self.capture.build,
                    'perimetre': self.capture.scope, 'avertissements_capture': self.capture.warnings,
                    'code_execute': False, 'equivalence_comportementale': 'Non vérifiée'}
        self.text_set(self.meta, json.dumps(metadata, ensure_ascii=False, indent=2))
        self.constants.delete(*self.constants.get_children())
        self.disasm.show('; Ouvrez cet onglet pour charger le désassemblage.', False)
        if self.engine and item.source:
            engine, encoding = self.engine, self.encoding.get()
            self._task(lambda: engine.structure(item, encoding), lambda data: self.structure_done(item, data))
        self.status.set(f'{item.kind} · Luau v{item.version} · {item.status} · {item.syntax}')
        self.tab_changed()

    def render_analysis(self, item):
        data = item.analysis
        services = ', '.join(data.get('services', [])) or 'Aucun appel littéral détecté'
        calls = '\n'.join(f"  L{entry['line']}  {entry['method']}" for entry in data.get('remotes', [])) or '  Aucun appel détecté'
        functions = '\n'.join(f"  L{entry['line']}  {entry['name']}" for entry in data.get('functions', [])[:80]) or '  Aucune fonction nommée'
        incoming = []
        if self.capture:
            for other in self.capture.scripts:
                if any(item.id in dep['ids'] for dep in resolve_dependencies(other, self.capture)):
                    incoming.append(other.path)
        content = (f"ÉTAT : {item.status}   ·   {item.syntax}\n"
                   f"{data.get('lines', 0)} lignes reconstruites · {len(data.get('functions', []))} fonctions\n\n"
                   f"SERVICES\n{services}\n\nAPPELS DISTANTS\n{calls}\n\nFONCTIONS\n{functions}\n\n"
                   'CONSOMMATEURS DÉTECTÉS DANS LE CODE DÉJÀ ANALYSÉ\n' + ('\n'.join(incoming[:30]) or 'Aucun') +
                   '\n\nLes relations sont déduites statiquement. Les chemins dynamiques peuvent rester non résolus.\n'
                   'Une syntaxe valide ne prouve pas l’équivalence avec le programme original.\n')
        if item.diagnostics:
            content += '\nDIAGNOSTICS\n' + '\n'.join(item.diagnostics[:30])
        self.text_set(self.analysis_text, content)
        for number, line in enumerate(content.splitlines(), 1):
            if line.isupper() and not line.startswith(' '):
                self.analysis_text.tag_add('heading', f'{number}.0', f'{number}.end')
        self.dependencies.delete(*self.dependencies.get_children())
        self.dep_targets = {}
        for index, dep in enumerate(resolve_dependencies(item, self.capture)):
            iid = f'd{index}'
            self.dependencies.insert('', 'end', iid=iid, values=(dep['expression'], dep['resolution']))
            self.dep_targets[iid] = dep['ids']

    def structure_done(self, item, data):
        if not self.selected or self.selected.id != item.id:
            return
        self.constants.delete(*self.constants.get_children())
        for proto in data.get('protos', []):
            for const in proto.get('constants', []):
                self.constants.insert('', 'end', values=(proto.get('name') or f"proto {proto['index']}", const['kind'], const['text']))

    def open_dependency(self, _=None):
        selected = self.dependencies.selection()
        if not selected:
            return
        targets = self.dep_targets.get(selected[0], [])
        if len(targets) == 1:
            self.focus_script(targets[0])
            self.notebook.select(0)
        elif targets:
            messagebox.showinfo('Chemin ambigu', 'Plusieurs instances correspondent à ce chemin.', parent=self)

    def tab_changed(self, _=None):
        if not self.selected or not self.engine:
            return
        if self.notebook.index(self.notebook.select()) == 3:
            item, engine, encoding = self.selected, self.engine, self.encoding.get()
            self.disasm.show('; Chargement du désassemblage…', False)
            self._task(lambda: engine.run(item, 'disasm', encoding),
                       lambda output: self.disasm.show(output, False) if self.selected is item else None)

    def update_row(self, item):
        if self.tree.exists(item.id):
            self.tree.item(item.id, values=(item.status,), tags=(item.status,))

    def update_counts(self):
        if not self.capture:
            return
        items = self.capture.scripts
        counts = (len(items), len({item.digest for item in items}), sum(bool(item.source) for item in items),
                  sum(item.status in ('Partiel', 'Échec') for item in items))
        for label, value in zip(self.count_labels, counts):
            label.configure(text=f'{value:,}'.replace(',', ' '))

    def decompile_all(self):
        if not self.capture or not self.need_engine() or self.batch_running:
            return
        self.batch_running = True
        self.encoding_combo.configure(state='disabled')
        self.cancel.clear()
        capture, engine, encoding, generation = self.capture, self.engine, self.encoding.get(), self.generation
        self.progress.configure(value=0, maximum=len(capture.scripts))
        self.status.set('Décompilation locale de la capture…')
        def batch():
            completed = 0
            # Deduplicate work by content, preserving all instance paths in the UI.
            groups = {}
            for item in capture.scripts:
                groups.setdefault(item.digest, []).append(item)
            def one(group):
                if self.cancel.is_set():
                    return []
                first = engine.decompile(group[0], encoding)
                for copy in group[1:]:
                    copy.source, copy.status, copy.diagnostics = first.source, first.status, list(first.diagnostics)
                    copy.analysis, copy.syntax = first.analysis, first.syntax
                return group
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [pool.submit(one, group) for group in groups.values()]
                for future in as_completed(futures):
                    items = future.result()
                    completed += len(items)
                    self.events.put((generation, 'progress', (completed, items)))
            return completed
        def done(total):
            self.batch_running = False
            self.encoding_combo.configure(state='readonly')
            self.rebuild_tree()
            self.update_counts()
            self.status.set(f'Lot {"arrêté" if self.cancel.is_set() else "terminé"} · {total} scripts traités. Aucun code récupéré exécuté.')
            if self.selected:
                self.display_script(self.selected)
        def failed():
            self.batch_running = False
            self.encoding_combo.configure(state='readonly')
        self._task(batch, done, on_error=failed)

    def stop(self):
        self.cancel.set()
        self.status.set('Arrêt demandé ; les opérations déjà lancées terminent leur travail.')

    def encoding_changed(self, *_):
        if self.batch_running:
            self.status.set('Le nouveau mode s’appliquera au prochain lot.')
            return
        if self.capture:
            for item in self.capture.scripts:
                item.source, item.status, item.analysis, item.structural = '', 'À décompiler', {}, {}
            self.rebuild_tree()
            self.update_counts()

    def live_capture(self):
        if self.batch_running:
            return
        self.status.set('Capture Roblox en lecture seule puis validation des conteneurs…')
        destination = self.local / 'captures' / ('capture-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        self._task(lambda: capture_live(ROOT, destination), lambda folder: self.open_path(folder))

    def find_next(self):
        query = self.find_text.get()
        if not query:
            self.find_entry.focus_set()
            return
        text = self.code.text
        text.tag_remove('active_match', '1.0', 'end')
        start = text.index('insert +1c')
        found = text.search(query, start, stopindex='end', nocase=True) or text.search(query, '1.0', stopindex='end', nocase=True)
        if found:
            text.tag_add('active_match', found, f'{found}+{len(query)}c')
            text.mark_set('insert', found)
            text.see(found)
            self.code.redraw()
        else:
            self.status.set('Texte introuvable dans le script affiché.')

    def focus_find(self):
        self.notebook.select(0)
        self.after_idle(self.find_entry.focus_set)

    def go_line(self):
        line = simpledialog.askinteger('Aller à la ligne', 'Numéro de ligne :', minvalue=1, parent=self)
        if line:
            self.notebook.select(0)
            self.code.go(line)

    def global_search(self):
        if not self.capture:
            return
        query = simpledialog.askstring('Rechercher dans le code',
                                        'Texte à rechercher dans les scripts déjà décompilés :', parent=self)
        if not query:
            return
        px = self.px
        window = tk.Toplevel(self)
        window.title('Recherche globale · ' + query)
        window.geometry(f'{px(1000)}x{px(530)}')
        self.skin(window, bg='surface')
        self.skin(tk.Label(window, text='Recherche littérale · 500 résultats maximum · code déjà décompilé',
                           anchor='w', padx=px(16), pady=px(14), font=self.fonts['small']),
                  bg='surface', fg='muted').pack(fill='x')
        table = ttk.Treeview(window, columns=('path', 'line', 'code'), show='headings')
        for key, title, width in [('path', 'Script', 360), ('line', 'Ligne', 60), ('code', 'Extrait', 550)]:
            table.heading(key, text=title, anchor='w')
            table.column(key, width=px(width))
        table.pack(fill='both', expand=True, padx=px(12), pady=(0, px(12)))
        self.hover_rows(table)
        self.on_theme(lambda t: set_title_bar(window, self.theme == 'dark'))
        hits = {}
        for script in self.capture.scripts:
            for line, source in enumerate(script.source.splitlines(), 1):
                if query.casefold() in source.casefold() and len(hits) < 500:
                    ident = table.insert('', 'end', values=(script.path, line, source.strip()[:300]))
                    hits[ident] = (script.id, line)
        def jump(_):
            selected = table.selection()
            if selected:
                ident, line = hits[selected[0]]
                self.focus_script(ident, line)
                self.notebook.select(0)
                window.destroy()
        table.bind('<Double-1>', jump)

    def export_source(self):
        if not self.selected or not self.selected.source:
            return
        name = re.sub(r'[^\w.-]', '_', self.selected.name) + '.luau'
        chosen = filedialog.asksaveasfilename(title='Exporter le code reconstruit', initialfile=name,
                                             defaultextension='.luau', filetypes=[('Luau', '*.luau')], parent=self)
        if chosen:
            Path(chosen).write_text(self.selected.source, encoding='utf-8')
            self.status.set('Script exporté : ' + chosen)

    def export_project(self):
        if not self.capture:
            return
        chosen = filedialog.askdirectory(title='Dossier parent de l’export', parent=self)
        if chosen:
            destination = Path(chosen) / ('RSE-export-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
            capture = self.capture
            self._task(lambda: export_capture(capture, destination),
                       lambda report: self.status.set(f'Export enregistré : {destination}'))

    def _task(self, function, callback, on_error=None):
        generation = self.generation
        future = self.workers.submit(function)
        def settled(done):
            try:
                self.events.put((generation, 'result', (callback, done.result())))
            except Exception as exc:
                self.events.put((generation, 'error', (str(exc), on_error)))
        future.add_done_callback(settled)

    def _poll(self):
        for _ in range(60):
            try:
                generation, kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            if generation != self.generation:
                continue
            if kind == 'result':
                callback, value = data
                callback(value)
            elif kind == 'error':
                message, handler = data
                if handler:
                    handler()
                self.status.set(message[:200])
                messagebox.showerror('Opération interrompue', message[:4000], parent=self)
            elif kind == 'progress':
                completed, items = data
                self.progress.configure(value=completed)
                for item in items:
                    self.update_row(item)
                self.status.set(f'{completed} / {len(self.capture.scripts)} scripts traités…')
                self.update_counts()
        self.after(100, self._poll)

    def close(self):
        self.cancel.set()
        self.workers.shutdown(wait=False, cancel_futures=True)
        self.save_settings()
        self.destroy()


def main():
    parser = argparse.ArgumentParser(description='Roblox Script Explorer')
    parser.add_argument('--capture', type=Path)
    parser.add_argument('--engine', type=Path)
    args = parser.parse_args()
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    app = Explorer(args.capture, args.engine)
    app.mainloop()


if __name__ == '__main__':
    main()
