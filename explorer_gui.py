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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from explorer_core import (Capture, Decompiler, ExplorerError, Script, TOKEN, analyze,
                           capture_live, export_capture, load_capture,
                           resolve_dependencies, write_json)

ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
BG, PANEL, CARD = '#10151e', '#171e2a', '#202a39'
TEXT, MUTED, ACCENT, GREEN = '#e7edf5', '#92a3ba', '#ffb454', '#79d5b4'


class CodeView(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg=BG)
        self.gutter = tk.Canvas(self, width=56, bg=BG, highlightthickness=0)
        self.gutter.pack(side='left', fill='y')
        self.text = tk.Text(self, wrap='none', bg=BG, fg=TEXT, insertbackground=TEXT,
                            selectbackground='#365473', relief='flat', borderwidth=0,
                            font=('Consolas', 11), padx=12, pady=14, undo=False,
                            tabs=('4c',), state='disabled')
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.text.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.text.xview)
        vertical.pack(side='right', fill='y')
        horizontal.pack(side='bottom', fill='x')
        self.text.pack(fill='both', expand=True)
        self.text.configure(yscrollcommand=lambda a, b: (vertical.set(a, b), self.redraw()),
                            xscrollcommand=horizontal.set)
        for tag, color in [('keyword', '#c6a0f6'), ('string', '#a7d99b'), ('number', '#f1b477'),
                           ('comment', '#72849a'), ('function', '#8bd5ee')]:
            self.text.tag_configure(tag, foreground=color)
        self.text.tag_configure('match', background='#685022', foreground='#ffffff')
        self.text.tag_configure('active_match', background='#c48a32', foreground='#10151e')
        self.text.bind('<Configure>', lambda e: self.redraw())
        self._pending = False

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
            self.gutter.create_text(43, box[1], text=line, anchor='ne', fill='#61758f', font=('Consolas', 10))
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


class Explorer(tk.Tk):
    def __init__(self, capture_path=None, engine_path=None):
        super().__init__()
        self.title('Roblox Script Explorer')
        self.geometry('1440x920')
        self.minsize(1060, 700)
        self.configure(bg=BG)
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
        self._style()
        self._build()
        self._configure_engine(engine_path or self.settings.get('engine') or ROOT / 'tools' / 'luaudec.exe', quiet=True)
        self.after(100, self._poll)
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

    def _style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), background=PANEL, foreground=TEXT)
        style.configure('TFrame', background=PANEL)
        style.configure('TLabel', background=PANEL, foreground=TEXT)
        style.configure('TButton', background=CARD, foreground=TEXT, borderwidth=0, padding=(14, 9))
        style.map('TButton', background=[('active', '#33445b')], foreground=[('disabled', '#69798e')])
        style.configure('Accent.TButton', background=ACCENT, foreground='#16202d')
        style.map('Accent.TButton', background=[('active', '#ffca85')])
        style.configure('TEntry', fieldbackground=BG, foreground=TEXT, insertcolor=TEXT, padding=8)
        style.configure('TCombobox', fieldbackground=BG, background=CARD, foreground=TEXT, padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', BG)], foreground=[('readonly', TEXT)])
        style.configure('Treeview', background=PANEL, fieldbackground=PANEL, foreground=TEXT,
                        borderwidth=0, rowheight=30, font=('Segoe UI', 10))
        style.configure('Treeview.Heading', background=CARD, foreground=MUTED, relief='flat', padding=8)
        style.map('Treeview', background=[('selected', '#2a4561')], foreground=[('selected', '#ffffff')])
        style.configure('TNotebook', background=BG, borderwidth=0)
        style.configure('TNotebook.Tab', background=PANEL, foreground=MUTED, padding=(20, 11))
        style.map('TNotebook.Tab', background=[('selected', CARD)], foreground=[('selected', ACCENT)])
        style.configure('Horizontal.TProgressbar', troughcolor=CARD, background=GREEN, borderwidth=0)
        self.option_add('*TCombobox*Listbox.background', PANEL)
        self.option_add('*TCombobox*Listbox.foreground', TEXT)

    def _build(self):
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
        self.configure(menu=menu)

        top = tk.Frame(self, bg=BG, padx=25, pady=20)
        top.pack(fill='x')
        tk.Label(top, text='RSE', bg=ACCENT, fg='#10151e', padx=13, pady=10,
                 font=('Segoe UI', 16, 'bold')).pack(side='left', padx=(0, 15))
        brand = tk.Frame(top, bg=BG)
        brand.pack(side='left')
        tk.Label(brand, text='Roblox Script Explorer', bg=BG, fg=TEXT,
                 font=('Segoe UI', 19, 'bold')).pack(anchor='w')
        tk.Label(brand, text='CAPTURER  /  COMPRENDRE  /  EXPLORER', bg=BG, fg=MUTED,
                 font=('Segoe UI', 9)).pack(anchor='w', pady=(3, 0))
        self.engine_label = tk.Label(top, text='MOTEUR NON CONFIGURÉ', bg=BG, fg=ACCENT, font=('Segoe UI', 9, 'bold'))
        self.engine_label.pack(side='right')

        toolbar = tk.Frame(self, bg=PANEL, padx=18, pady=10)
        toolbar.pack(fill='x', padx=20)
        for label, action, accent in [('Ouvrir capture', self.open_folder, True), ('Capturer Roblox', self.live_capture, False),
                                      ('Décompiler tout', self.decompile_all, False), ('Exporter', self.export_project, False)]:
            ttk.Button(toolbar, text=label, command=action, style='Accent.TButton' if accent else 'TButton').pack(side='left', padx=4)
        ttk.Button(toolbar, text='Arrêter', command=self.stop).pack(side='right', padx=4)
        self.encoding_combo = ttk.Combobox(toolbar, textvariable=self.encoding, values=('roblox', 'standard', 'auto'),
                                           state='readonly', width=11)
        self.encoding_combo.pack(side='right', padx=10)
        self.encoding.trace_add('write', self.encoding_changed)

        cards = tk.Frame(self, bg=BG, padx=25, pady=15)
        cards.pack(fill='x')
        self.count_labels = []
        for title, value in [('SCRIPTS', '—'), ('CONTENUS DISTINCTS', '—'), ('CODE RECONSTRUIT', '—'), ('À EXAMINER', '—')]:
            cell = tk.Frame(cards, bg=BG)
            cell.pack(side='left', fill='x', expand=True)
            label = tk.Label(cell, text=value, bg=BG, fg=TEXT, font=('Segoe UI', 22, 'bold'))
            label.pack(anchor='w')
            tk.Label(cell, text=title, bg=BG, fg=MUTED, font=('Segoe UI', 8, 'bold')).pack(anchor='w')
            self.count_labels.append(label)

        self.panes = tk.PanedWindow(self, orient='horizontal', bg=BG, sashwidth=7, borderwidth=0)
        self.panes.pack(fill='both', expand=True, padx=20)
        left = tk.Frame(self.panes, bg=PANEL)
        self.panes.add(left, minsize=260, width=355)
        self.capture_label = tk.Label(left, text='AUCUNE CAPTURE', bg=PANEL, fg=MUTED, anchor='w', padx=15, pady=12,
                                      font=('Segoe UI', 9, 'bold'))
        self.capture_label.pack(fill='x')
        searchrow = tk.Frame(left, bg=PANEL, padx=10)
        searchrow.pack(fill='x')
        entry = ttk.Entry(searchrow, textvariable=self.filter)
        entry.pack(fill='x', pady=(0, 8))
        ttk.Combobox(searchrow, textvariable=self.kind_filter,
                     values=('Tous', 'LocalScript', 'ModuleScript', 'Reconstruit', 'Partiel', 'Échec'),
                     state='readonly').pack(fill='x', pady=(0, 8))
        ttk.Button(searchrow, text='Rechercher dans le code…', command=self.global_search).pack(fill='x', pady=(0, 8))
        self.filter.trace_add('write', self.filter_changed)
        self.kind_filter.trace_add('write', self.filter_changed)
        treeframe = tk.Frame(left, bg=PANEL)
        treeframe.pack(fill='both', expand=True)
        scroll = ttk.Scrollbar(treeframe)
        scroll.pack(side='right', fill='y')
        self.tree = ttk.Treeview(treeframe, columns=('status',), show='tree headings', yscrollcommand=scroll.set,
                                 selectmode='browse')
        self.tree.heading('#0', text='ARBORESCENCE')
        self.tree.heading('status', text='ÉTAT')
        self.tree.column('#0', width=220, minwidth=100)
        self.tree.column('status', width=95, minwidth=80, stretch=False)
        self.tree.pack(fill='both', expand=True)
        scroll.configure(command=self.tree.yview)
        self.tree.bind('<<TreeviewSelect>>', self.select_script)
        self.tree.tag_configure('Partiel', foreground=ACCENT)
        self.tree.tag_configure('Échec', foreground='#f298a5')

        right = tk.Frame(self.panes, bg=BG)
        self.panes.add(right, minsize=620)
        header = tk.Frame(right, bg=PANEL, padx=18, pady=14)
        header.pack(fill='x')
        self.script_label = tk.Label(header, text='Votre espace d’analyse', bg=PANEL, fg=TEXT,
                                     font=('Segoe UI', 15, 'bold'), anchor='w')
        self.script_label.pack(fill='x')
        self.path_label = tk.Label(header, text='Ouvrez un dossier de capture ou un fichier .luac.', bg=PANEL,
                                   fg=MUTED, anchor='w', font=('Segoe UI', 9), wraplength=850)
        self.path_label.pack(fill='x', pady=(5, 0))
        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill='both', expand=True)
        code_tab = ttk.Frame(self.notebook)
        self.notebook.add(code_tab, text='Code Luau')
        findbar = tk.Frame(code_tab, bg=PANEL, padx=10, pady=7)
        findbar.pack(fill='x')
        self.find_text = tk.StringVar()
        self.find_entry = ttk.Entry(findbar, textvariable=self.find_text, width=28)
        self.find_entry.pack(side='left', padx=(0, 8))
        self.find_entry.bind('<Return>', lambda e: self.find_next())
        ttk.Button(findbar, text='Suivant', command=self.find_next).pack(side='left')
        ttk.Button(findbar, text='Aller à la ligne', command=self.go_line).pack(side='left', padx=6)
        ttk.Button(findbar, text='Exporter .luau', command=self.export_source).pack(side='right')
        self.code = CodeView(code_tab)
        self.code.pack(fill='both', expand=True)
        self.code.show('-- Bienvenue dans Roblox Script Explorer.\n\n-- Ouvrez une capture pour explorer son arborescence.\n-- Choisissez un script pour reconstruire son code localement.\n-- Aucun script récupéré n’est exécuté.', True)

        analysis_tab = tk.Frame(self.notebook, bg=PANEL)
        self.notebook.add(analysis_tab, text='Analyse')
        self.analysis_text = self._text(analysis_tab, font=('Segoe UI', 11), height=15)
        self.analysis_text.pack(fill='both', expand=True, padx=15, pady=12)
        tk.Label(analysis_tab, text='DÉPENDANCES INFÉRÉES · double-cliquez pour ouvrir un chemin résolu',
                 bg=PANEL, fg=MUTED, anchor='w', padx=15, pady=10).pack(fill='x')
        self.dependencies = ttk.Treeview(analysis_tab, columns=('expression', 'resolution'), show='headings', height=7)
        self.dependencies.heading('expression', text='require(...)')
        self.dependencies.heading('resolution', text='Résolution statique')
        self.dependencies.column('expression', width=450)
        self.dependencies.column('resolution', width=190)
        self.dependencies.pack(fill='x', padx=15, pady=(0, 15))
        self.dependencies.bind('<Double-1>', self.open_dependency)
        self.dep_targets = {}

        constants_tab = ttk.Frame(self.notebook)
        self.notebook.add(constants_tab, text='Constantes')
        self.constants = ttk.Treeview(constants_tab, columns=('function', 'kind', 'value'), show='headings')
        for key, title, width in [('function', 'Fonction', 180), ('kind', 'Type', 100), ('value', 'Valeur', 650)]:
            self.constants.heading(key, text=title)
            self.constants.column(key, width=width, stretch=key == 'value')
        const_scroll = ttk.Scrollbar(constants_tab, command=self.constants.yview)
        const_scroll.pack(side='right', fill='y')
        self.constants.configure(yscrollcommand=const_scroll.set)
        self.constants.pack(fill='both', expand=True)

        disasm_tab = ttk.Frame(self.notebook)
        self.notebook.add(disasm_tab, text='Désassemblage')
        self.disasm = CodeView(disasm_tab)
        self.disasm.pack(fill='both', expand=True)
        meta_tab = ttk.Frame(self.notebook)
        self.notebook.add(meta_tab, text='Métadonnées')
        self.meta = self._text(meta_tab, font=('Consolas', 10))
        self.meta.pack(fill='both', expand=True, padx=12, pady=12)
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)

        bottom = tk.Frame(self, bg=BG, padx=22, pady=10)
        bottom.pack(side='bottom', before=self.panes, fill='x')
        self.progress = ttk.Progressbar(bottom, mode='determinate', length=200)
        self.progress.pack(side='right', padx=10)
        tk.Label(bottom, textvariable=self.status, bg=BG, fg=MUTED, anchor='w').pack(side='left', fill='x', expand=True)
        self._filter_after = None

    def _text(self, parent, **kwargs):
        return tk.Text(parent, bg=PANEL, fg=TEXT, relief='flat', wrap='word', padx=7, pady=7,
                       selectbackground='#365473', state='disabled', **kwargs)

    def text_set(self, widget, content):
        widget.configure(state='normal')
        widget.delete('1.0', 'end')
        widget.insert('1.0', content)
        widget.configure(state='disabled')

    def _configure_engine(self, path, quiet=False):
        try:
            self.engine = Decompiler(Path(path), self.local / 'cache', ROOT / 'tools' / 'luau-compile.exe')
            self.settings['engine'] = str(self.engine.executable)
            self.engine_label.configure(text='●  MOTEUR LOCAL PRÊT', fg=GREEN)
            self.save_settings()
            return True
        except (ExplorerError, OSError) as exc:
            self.engine = None
            self.engine_label.configure(text='MOTEUR À CONFIGURER', fg=ACCENT)
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
        self.capture_label.configure(text='CAPTURE · ' + capture.build.replace('version-', '')[:22])
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
        window = tk.Toplevel(self)
        window.title('Recherche globale · ' + query)
        window.geometry('1000x530')
        window.configure(bg=PANEL)
        tk.Label(window, text='Recherche littérale · 500 résultats maximum · code déjà décompilé',
                 bg=PANEL, fg=MUTED, padx=15, pady=15).pack(fill='x')
        table = ttk.Treeview(window, columns=('path', 'line', 'code'), show='headings')
        for key, title, width in [('path', 'Script', 360), ('line', 'Ligne', 60), ('code', 'Extrait', 550)]:
            table.heading(key, text=title)
            table.column(key, width=width)
        table.pack(fill='both', expand=True, padx=12, pady=12)
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
