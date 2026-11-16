import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_PATH = APP_DIR / 'alphaclube source.pmp'
DEFAULT_HTML_PATH = APP_DIR / 'alphaclube source.html'
BACKUP_DIR = APP_DIR / 'backups'
CONFIG_FILE = APP_DIR / 'clubepatcher_config.json'
CURRENT_SERVER_URL = 'https://alphaclube-websim--todthefanatic45028.on.websim.com'
SERVER_MARKER_START = '<!-- ClubePatcher server URL start -->'
SERVER_MARKER_END = '<!-- ClubePatcher server URL end -->'


def load_config(path: Path):
    defaults = {
        'last_source': str(DEFAULT_SOURCE_PATH),
        'last_html': str(DEFAULT_HTML_PATH),
    }
    if not path.exists():
        return defaults
    try:
        defaults.update(json.loads(path.read_text(encoding='utf-8')))
    except (OSError, json.JSONDecodeError):
        pass
    return defaults


def save_config(path: Path, config):
    path.write_text(json.dumps(config, indent=2), encoding='utf-8')


def normalize_project_data(data):
    if not isinstance(data, dict) or not isinstance(data.get('targets'), list):
        raise ValueError('The archive does not contain a valid PenguinMod project.')
    data.setdefault('monitors', [])
    return data


def project_counts(project):
    targets = project.get('targets', [])
    costumes = sum(len(target.get('costumes', [])) for target in targets)
    blocks = sum(len(target.get('blocks', {})) for target in targets)
    return {
        'sprites': sum(not target.get('isStage', False) for target in targets),
        'backdrops': sum(len(target.get('costumes', [])) for target in targets if target.get('isStage')),
        'costumes': costumes,
        'blocks': blocks,
    }


def swap_costume_artwork(target_a, index_a, target_b, index_b):
    costume_a = target_a['costumes'][index_a]
    costume_b = target_b['costumes'][index_b]
    name_a = costume_a.get('name', '')
    name_b = costume_b.get('name', '')
    swapped_a = deepcopy(costume_b)
    swapped_b = deepcopy(costume_a)
    swapped_a['name'] = name_a
    swapped_b['name'] = name_b
    target_a['costumes'][index_a] = swapped_a
    target_b['costumes'][index_b] = swapped_b
    return name_a, name_b


def build_block(opcode, next_id=None, parent_id=None, inputs=None, fields=None, top_level=False, x=0, y=0):
    block = {
        'opcode': opcode,
        'next': next_id,
        'parent': parent_id,
        'inputs': inputs or {},
        'fields': fields or {},
        'shadow': False,
        'topLevel': top_level,
    }
    if top_level:
        block['x'] = x
        block['y'] = y
    return block


def build_clicker_minigame(project, game_name):
    game_name = game_name.strip()
    if not game_name:
        raise ValueError('Enter a name for the minigame.')
    if len(game_name) > 48:
        raise ValueError('Minigame names must be 48 characters or fewer.')

    targets = project.get('targets', [])
    target_name = f'Minigames//{game_name}'
    existing_names = {target.get('name') for target in targets}
    if target_name in existing_names:
        raise ValueError(f'A minigame named "{game_name}" already exists.')

    score_id = uuid.uuid4().hex
    sprite_id = uuid.uuid4().hex
    score_name = f'{game_name} Score'
    title = html.escape(game_name)
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360" viewBox="0 0 480 360">'
        '<defs><linearGradient id="bg" x2="0" y2="1">'
        '<stop offset="0" stop-color="#16304f"/><stop offset="1" stop-color="#091322"/>'
        '</linearGradient></defs>'
        '<rect width="480" height="360" rx="26" fill="url(#bg)"/>'
        '<text x="240" y="58" text-anchor="middle" fill="#f3f7ff" '
        'font-family="sans-serif" font-size="26" font-weight="700">'
        f'{title}</text>'
        '<circle cx="240" cy="188" r="82" fill="#f6c453" stroke="#fff1b7" stroke-width="8"/>'
        '<circle cx="240" cy="188" r="57" fill="#ff914d"/>'
        '<text x="240" y="198" text-anchor="middle" fill="#17243a" '
        'font-family="sans-serif" font-size="22" font-weight="700">CLICK!</text>'
        '<text x="240" y="302" text-anchor="middle" fill="#c6d7ef" '
        'font-family="sans-serif" font-size="16">M: start / reset  |  X: exit</text>'
        '</svg>'
    ).encode('utf-8')
    asset_id = hashlib.md5(svg).hexdigest()
    costume = {
        'name': 'Click target',
        'bitmapResolution': 1,
        'dataFormat': 'svg',
        'assetId': asset_id,
        'md5ext': f'{asset_id}.svg',
        'rotationCenterX': 240,
        'rotationCenterY': 180,
    }

    flag_id, flag_hide_id = uuid.uuid4().hex, uuid.uuid4().hex
    start_id, reset_id, show_id, move_id, say_id = [uuid.uuid4().hex for _ in range(5)]
    click_id, score_id_block = uuid.uuid4().hex, uuid.uuid4().hex
    exit_id, exit_hide_id = uuid.uuid4().hex, uuid.uuid4().hex
    blocks = {
        flag_id: build_block('event_whenflagclicked', flag_hide_id, top_level=True, x=24, y=24),
        flag_hide_id: build_block('looks_hide', parent_id=flag_id),
        start_id: build_block(
            'event_whenkeypressed',
            reset_id,
            fields={'KEY_OPTION': ['m', None]},
            top_level=True,
            x=280,
            y=24,
        ),
        reset_id: build_block(
            'data_setvariableto',
            show_id,
            parent_id=start_id,
            inputs={'VALUE': [1, [10, '0']]},
            fields={'VARIABLE': [score_name, score_id, '']},
        ),
        show_id: build_block('looks_show', move_id, parent_id=reset_id),
        move_id: build_block(
            'motion_gotoxy',
            say_id,
            parent_id=show_id,
            inputs={'X': [1, [4, '0']], 'Y': [1, [4, '0']]},
        ),
        say_id: build_block(
            'looks_say',
            parent_id=move_id,
            inputs={'MESSAGE': [1, [10, 'Click the target to score!']]},
        ),
        click_id: build_block('event_whenthisspriteclicked', score_id_block, top_level=True, x=24, y=240),
        score_id_block: build_block(
            'data_changevariableby',
            parent_id=click_id,
            inputs={'VALUE': [1, [4, '1']]},
            fields={'VARIABLE': [score_name, score_id, '']},
        ),
        exit_id: build_block(
            'event_whenkeypressed',
            exit_hide_id,
            fields={'KEY_OPTION': ['x', None]},
            top_level=True,
            x=280,
            y=240,
        ),
        exit_hide_id: build_block('looks_hide', parent_id=exit_id),
    }
    target = {
        'isStage': False,
        'name': target_name,
        'variables': {score_id: [score_name, 0]},
        'lists': {},
        'broadcasts': {},
        'customVars': [],
        'blocks': blocks,
        'comments': {},
        'currentCostume': 0,
        'costumes': [costume],
        'sounds': [],
        'id': sprite_id,
        'volume': 100,
        'layerOrder': max((t.get('layerOrder', 0) for t in targets), default=0) + 1,
        'visible': False,
        'x': 0,
        'y': 0,
        'size': 100,
        'direction': 90,
        'draggable': False,
        'rotationStyle': 'all around',
        'extensionData': {},
    }
    monitor = {
        'id': score_id,
        'mode': 'default',
        'opcode': 'data_variable',
        'params': {'VARIABLE': score_name},
        'spriteName': target_name,
        'value': 0,
        'width': 0,
        'height': 0,
        'x': 8,
        'y': 8,
        'visible': True,
        'variableType': None,
        'variableId': score_id,
        'sliderMin': 0,
        'sliderMax': 100,
        'isDiscrete': True,
    }
    return target, monitor, {costume['md5ext']: svg}


def validate_server_url(url):
    url = url.strip().rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise ValueError('Enter a complete server URL starting with http:// or https://.')
    if any(character.isspace() for character in url):
        raise ValueError('The server URL cannot contain spaces.')
    return url


def build_server_url_script(url):
    js_value = json.dumps(url).replace('<', '\\u003c')
    return (
        f'{SERVER_MARKER_START}\n'
        f'<script>window.ALPHACLUBE_SERVER = {js_value};</script>\n'
        f'{SERVER_MARKER_END}\n'
    ).encode('utf-8')


def patch_html_server_url(source_path: Path, output_path: Path, server_url: str):
    server_url = validate_server_url(server_url)
    content = source_path.read_bytes()

    start_marker = SERVER_MARKER_START.encode('utf-8')
    end_marker = SERVER_MARKER_END.encode('utf-8')
    start = content.find(start_marker)
    if start >= 0:
        end = content.find(end_marker, start + len(start_marker))
        if end < 0:
            raise ValueError('Found an incomplete ClubePatcher URL marker in the HTML.')
        content = content[:start] + content[end + len(end_marker):]

    old_url = CURRENT_SERVER_URL.encode('ascii')
    replacement_count = content.count(old_url)
    if replacement_count:
        content = content.replace(old_url, server_url.encode('utf-8'))

    closing_head = re.search(rb'</head\s*>', content, flags=re.IGNORECASE)
    if not closing_head:
        raise ValueError('Could not find </head> in the selected HTML file.')
    content = content[:closing_head.start()] + build_server_url_script(server_url) + content[closing_head.start():]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = output_path.with_name(f'{output_path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temp_path.write_bytes(content)
        shutil.copystat(source_path, temp_path)
        temp_path.replace(output_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    return server_url, replacement_count


class ClubePatcherApp:
    def __init__(self, root):
        self.root = root
        self.root.title('ClubePatcher - AlphaClube Modding Tool')
        self.root.geometry('1180x780')
        self.root.minsize(1000, 660)
        self.root.configure(bg='#0b1220')

        self.config = load_config(CONFIG_FILE)
        self.project_path = None
        self.html_path = Path(self.config.get('last_html', str(DEFAULT_HTML_PATH)))
        self.project_data = None
        self.original_project_data = None
        self.asset_updates = {}
        self.project_modified = False
        self.sprite_names = []

        self._build_ui()
        self._load_last_project()

    def _build_ui(self):
        header = tk.Frame(self.root, bg='#101d34', padx=18, pady=12)
        header.pack(fill='x')
        tk.Label(
            header,
            text='ClubePatcher',
            font=('Segoe UI', 23, 'bold'),
            fg='#eef6ff',
            bg='#101d34',
        ).pack(side='left')
        self.status_var = tk.StringVar(value='No project loaded')
        tk.Label(
            header,
            textvariable=self.status_var,
            font=('Segoe UI', 10),
            fg='#a9bddf',
            bg='#101d34',
        ).pack(side='right')

        toolbar = tk.Frame(self.root, bg='#0b1220', padx=12, pady=10)
        toolbar.pack(fill='x')
        actions = [
            ('Load PMP', self.load_source_dialog, '#4d82ff'),
            ('Open HTML preview', self.open_html_preview, '#167d9a'),
            ('Create backup', self.create_backup, '#2cb67d'),
            ('Apply PMP mods', self.apply_project_changes, '#ff9f43'),
            ('Export PMP', self.export_project, '#6a5cff'),
            ('Reset changes', self.reset_project, '#e76f51'),
        ]
        for label, command, color in actions:
            tk.Button(
                toolbar,
                text=label,
                command=command,
                bg=color,
                fg='white',
                font=('Segoe UI', 9, 'bold'),
                relief='flat',
                padx=10,
                pady=8,
            ).pack(side='left', padx=(0, 8))

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill='both', expand=True, padx=12, pady=(0, 8))

        self.project_tab = tk.Frame(self.tabs, bg='#111d2d', padx=12, pady=12)
        self.costume_tab = tk.Frame(self.tabs, bg='#111d2d', padx=16, pady=16)
        self.minigame_tab = tk.Frame(self.tabs, bg='#111d2d', padx=16, pady=16)
        self.homepage_tab = tk.Frame(self.tabs, bg='#111d2d', padx=16, pady=16)
        self.more_tab = tk.Frame(self.tabs, bg='#111d2d', padx=16, pady=16)
        self.tabs.add(self.project_tab, text='Project')
        self.tabs.add(self.costume_tab, text='Costume Swapper')
        self.tabs.add(self.minigame_tab, text='Minigame Creator')
        self.tabs.add(self.homepage_tab, text='Homepage Changer')
        self.tabs.add(self.more_tab, text='More')

        self._build_project_tab()
        self._build_costume_tab()
        self._build_minigame_tab()
        self._build_homepage_tab()
        self._build_more_tab()

        log_frame = tk.LabelFrame(self.root, text='Activity', bg='#111d2d', fg='#eef6ff', padx=8, pady=6)
        log_frame.pack(fill='x', padx=12, pady=(0, 12))
        self.log_box = tk.Text(log_frame, bg='#0c1522', fg='#dfeaff', height=5, wrap='word')
        self.log_box.pack(fill='x')
        self.log_box.config(state='disabled')

    def _build_project_tab(self):
        tk.Label(
            self.project_tab,
            text='AlphaClube project overview',
            font=('Segoe UI', 17, 'bold'),
            bg='#111d2d',
            fg='#eef6ff',
        ).pack(anchor='w')
        self.summary_var = tk.StringVar(value='Load an AlphaClube .pmp file to begin.')
        tk.Label(
            self.project_tab,
            textvariable=self.summary_var,
            justify='left',
            font=('Segoe UI', 10),
            bg='#111d2d',
            fg='#c5d5ec',
        ).pack(anchor='w', pady=(8, 12))

        self.project_tree = ttk.Treeview(
            self.project_tab,
            columns=('kind', 'costumes', 'blocks'),
            show='tree headings',
            height=20,
        )
        self.project_tree.heading('#0', text='Sprite / target')
        self.project_tree.heading('kind', text='Type')
        self.project_tree.heading('costumes', text='Costumes')
        self.project_tree.heading('blocks', text='Blocks')
        self.project_tree.column('#0', width=450, anchor='w')
        self.project_tree.column('kind', width=100, anchor='center')
        self.project_tree.column('costumes', width=100, anchor='center')
        self.project_tree.column('blocks', width=100, anchor='center')
        self.project_tree.pack(fill='both', expand=True)

    def _build_costume_tab(self):
        tk.Label(
            self.costume_tab,
            text='Swap artwork between two costume slots',
            font=('Segoe UI', 16, 'bold'),
            bg='#111d2d',
            fg='#eef6ff',
        ).pack(anchor='w')
        tk.Label(
            self.costume_tab,
            text='Choose two sprites and costumes. The artwork and rotation centers swap; the costume slot names stay put.',
            wraplength=850,
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w', pady=(6, 18))

        self.sprite_a_var = tk.StringVar()
        self.sprite_b_var = tk.StringVar()
        self.costume_a_var = tk.StringVar()
        self.costume_b_var = tk.StringVar()

        columns = tk.Frame(self.costume_tab, bg='#111d2d')
        columns.pack(fill='x', anchor='n')
        self.sprite_a_combo, self.costume_a_combo = self._add_costume_picker(
            columns, 'Sprite A', self.sprite_a_var, self.costume_a_var, self._on_sprite_a_change
        )
        self.sprite_b_combo, self.costume_b_combo = self._add_costume_picker(
            columns, 'Sprite B', self.sprite_b_var, self.costume_b_var, self._on_sprite_b_change
        )

        tk.Button(
            self.costume_tab,
            text='Swap costume artwork',
            command=self.swap_costumes,
            bg='#4d82ff',
            fg='white',
            font=('Segoe UI', 11, 'bold'),
            relief='flat',
            padx=14,
            pady=10,
        ).pack(anchor='w', pady=24)
        tk.Label(
            self.costume_tab,
            text='Swaps are staged in memory. Use “Apply PMP mods” or “Export PMP” to save them.',
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w')

    def _add_costume_picker(self, parent, title, sprite_var, costume_var, callback):
        frame = tk.LabelFrame(parent, text=title, bg='#111d2d', fg='#eef6ff', padx=12, pady=12)
        frame.pack(side='left', fill='x', expand=True, padx=(0, 12))
        tk.Label(frame, text='Target sprite', bg='#111d2d', fg='#dfeaff').pack(anchor='w')
        sprite_combo = ttk.Combobox(frame, textvariable=sprite_var, state='readonly', width=48)
        sprite_combo.pack(fill='x', pady=(4, 12))
        sprite_combo.bind('<<ComboboxSelected>>', callback)
        tk.Label(frame, text='Costume slot', bg='#111d2d', fg='#dfeaff').pack(anchor='w')
        costume_combo = ttk.Combobox(frame, textvariable=costume_var, state='readonly', width=48)
        costume_combo.pack(fill='x', pady=(4, 0))
        return sprite_combo, costume_combo

    def _build_minigame_tab(self):
        tk.Label(
            self.minigame_tab,
            text='Create a playable starter minigame in the PMP',
            font=('Segoe UI', 16, 'bold'),
            bg='#111d2d',
            fg='#eef6ff',
        ).pack(anchor='w')
        tk.Label(
            self.minigame_tab,
            text='Creates a new sprite, a custom SVG target, score variable, and Scratch blocks. In PenguinMod, press M to start, click the target to score, and press X to exit.',
            wraplength=850,
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w', pady=(6, 20))

        form = tk.Frame(self.minigame_tab, bg='#111d2d')
        form.pack(anchor='w', fill='x')
        tk.Label(form, text='Minigame name', bg='#111d2d', fg='#dfeaff').pack(anchor='w')
        self.minigame_name_var = tk.StringVar()
        tk.Entry(
            form,
            textvariable=self.minigame_name_var,
            width=48,
            bg='#0c1522',
            fg='white',
            insertbackground='white',
        ).pack(anchor='w', pady=(4, 12))

        tk.Label(
            form,
            text='Template: Click-to-score',
            bg='#111d2d',
            fg='#dfeaff',
            font=('Segoe UI', 10, 'bold'),
        ).pack(anchor='w')
        tk.Button(
            form,
            text='Create minigame sprite',
            command=self.create_minigame,
            bg='#4d82ff',
            fg='white',
            font=('Segoe UI', 11, 'bold'),
            relief='flat',
            padx=14,
            pady=10,
        ).pack(anchor='w', pady=18)

    def _build_homepage_tab(self):
        tk.Label(
            self.homepage_tab,
            text='Change the AlphaClube server URL',
            font=('Segoe UI', 16, 'bold'),
            bg='#111d2d',
            fg='#eef6ff',
        ).pack(anchor='w')
        tk.Label(
            self.homepage_tab,
            text='The bundled HTML reads window.ALPHACLUBE_SERVER. The known current host is shown below; choose another URL and save a patched HTML copy.',
            wraplength=850,
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w', pady=(6, 16))

        tk.Label(self.homepage_tab, text='Current host from request', bg='#111d2d', fg='#dfeaff').pack(anchor='w')
        tk.Label(
            self.homepage_tab,
            text=CURRENT_SERVER_URL,
            bg='#0c1522',
            fg='#a9bddf',
            padx=8,
            pady=7,
            anchor='w',
        ).pack(fill='x', pady=(4, 12))

        tk.Label(self.homepage_tab, text='Replacement server URL', bg='#111d2d', fg='#dfeaff').pack(anchor='w')
        self.server_url_var = tk.StringVar()
        tk.Entry(
            self.homepage_tab,
            textvariable=self.server_url_var,
            bg='#0c1522',
            fg='white',
            insertbackground='white',
        ).pack(fill='x', pady=(4, 12))

        path_row = tk.Frame(self.homepage_tab, bg='#111d2d')
        path_row.pack(fill='x', pady=(0, 16))
        self.html_path_var = tk.StringVar(value=str(self.html_path))
        tk.Entry(
            path_row,
            textvariable=self.html_path_var,
            bg='#0c1522',
            fg='white',
            insertbackground='white',
        ).pack(side='left', fill='x', expand=True, padx=(0, 8))
        tk.Button(
            path_row,
            text='Choose HTML',
            command=self.choose_html_file,
            bg='#314969',
            fg='white',
            relief='flat',
            padx=12,
            pady=7,
        ).pack(side='right')

        tk.Button(
            self.homepage_tab,
            text='Save patched HTML copy',
            command=self.export_html_with_server,
            bg='#4d82ff',
            fg='white',
            font=('Segoe UI', 11, 'bold'),
            relief='flat',
            padx=14,
            pady=10,
        ).pack(anchor='w')
        tk.Label(
            self.homepage_tab,
            text='The original HTML is kept unchanged. The replacement is injected before the game loads and can be previewed with Open HTML preview.',
            wraplength=850,
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w', pady=(14, 0))

    def _build_more_tab(self):
        tk.Label(
            self.more_tab,
            text='More project tools',
            font=('Segoe UI', 16, 'bold'),
            bg='#111d2d',
            fg='#eef6ff',
        ).pack(anchor='w')
        tk.Label(
            self.more_tab,
            text='ClubePatcher stages mod changes safely. Backups and exports preserve the rest of the original project archive.',
            wraplength=850,
            bg='#111d2d',
            fg='#b8c9e2',
        ).pack(anchor='w', pady=(6, 18))
        tk.Button(
            self.more_tab,
            text='Open backup folder',
            command=self.open_backup_folder,
            bg='#314969',
            fg='white',
            relief='flat',
            padx=12,
            pady=8,
        ).pack(anchor='w')

    def log(self, message):
        self.log_box.config(state='normal')
        self.log_box.insert('end', f'{message}\n')
        self.log_box.see('end')
        self.log_box.config(state='disabled')

    def _load_last_project(self):
        source = Path(self.config.get('last_source', str(DEFAULT_SOURCE_PATH)))
        if source.is_file():
            self.load_project(source)
        else:
            self._update_project_view()
            self.log('Load an AlphaClube .pmp project to use the costume and minigame tools.')

    def load_project(self, source_path: Path):
        try:
            with zipfile.ZipFile(source_path, 'r') as archive:
                try:
                    project_data = json.loads(archive.read('project.json').decode('utf-8'))
                except KeyError as exc:
                    raise ValueError('project.json was not found in the selected archive.') from exc
        except (OSError, zipfile.BadZipFile, json.JSONDecodeError, ValueError) as exc:
            messagebox.showerror('Could not load project', str(exc))
            return

        try:
            self.project_data = normalize_project_data(project_data)
        except ValueError as exc:
            messagebox.showerror('Invalid project', str(exc))
            return

        self.project_path = source_path.resolve()
        self.original_project_data = deepcopy(self.project_data)
        self.asset_updates = {}
        self.project_modified = False
        self.config['last_source'] = str(self.project_path)
        save_config(CONFIG_FILE, self.config)
        self._refresh_sprite_lists()
        self._update_project_view()
        self.status_var.set(f'Loaded {self.project_path.name}')
        self.log(f'Loaded project: {self.project_path}')

    def load_source_dialog(self):
        path = filedialog.askopenfilename(
            title='Select AlphaClube PenguinMod project',
            filetypes=[('PMP project', '*.pmp'), ('ZIP project', '*.zip'), ('All files', '*.*')],
            initialdir=str(APP_DIR),
        )
        if path:
            self.load_project(Path(path))

    def _update_project_view(self):
        for row in self.project_tree.get_children():
            self.project_tree.delete(row)
        if not self.project_data:
            self.summary_var.set('No project loaded. Load an AlphaClube .pmp file to begin.')
            return

        counts = project_counts(self.project_data)
        target_count = len(self.project_data.get('targets', []))
        self.summary_var.set(
            f'{self.project_path.name if self.project_path else "Project"}'
            f'  |  {counts["sprites"]} sprites  |  {counts["backdrops"]} backdrops'
            f'  |  {counts["costumes"]} costumes  |  {counts["blocks"]:,} blocks'
            f'  |  {target_count} targets'
            f'  |  {"Unsaved changes" if self.project_modified else "Saved state"}'
        )
        for target in self.project_data.get('targets', []):
            self.project_tree.insert(
                '',
                'end',
                text=target.get('name', 'Unnamed target'),
                values=(
                    'Stage' if target.get('isStage') else 'Sprite',
                    len(target.get('costumes', [])),
                    len(target.get('blocks', {})),
                ),
            )

    def _mark_project_modified(self, message):
        self.project_modified = True
        self.status_var.set('Unsaved PMP changes')
        self._update_project_view()
        self.log(message)

    def _refresh_sprite_lists(self):
        if not self.project_data:
            self.sprite_names = []
        else:
            self.sprite_names = sorted(
                (
                    target.get('name', '')
                    for target in self.project_data.get('targets', [])
                    if not target.get('isStage')
                ),
                key=str.casefold,
            )
        for combo, variable in (
            (self.sprite_a_combo, self.sprite_a_var),
            (self.sprite_b_combo, self.sprite_b_var),
        ):
            combo['values'] = self.sprite_names
            variable.set(self.sprite_names[0] if self.sprite_names else '')
        self._on_sprite_a_change()
        self._on_sprite_b_change()

    def _target_by_name(self, name):
        if not self.project_data:
            return None
        return next(
            (target for target in self.project_data['targets'] if not target.get('isStage') and target.get('name') == name),
            None,
        )

    def _refresh_costume_combo(self, target_name, combo, variable):
        target = self._target_by_name(target_name)
        costumes = target.get('costumes', []) if target else []
        names = [costume.get('name', f'Costume {index + 1}') for index, costume in enumerate(costumes)]
        combo['values'] = names
        variable.set(names[0] if names else '')

    def _on_sprite_a_change(self, event=None):
        self._refresh_costume_combo(self.sprite_a_var.get(), self.costume_a_combo, self.costume_a_var)

    def _on_sprite_b_change(self, event=None):
        self._refresh_costume_combo(self.sprite_b_var.get(), self.costume_b_combo, self.costume_b_var)

    def swap_costumes(self):
        if not self.project_data:
            messagebox.showwarning('No project loaded', 'Load a .pmp project before swapping costumes.')
            return
        target_a = self._target_by_name(self.sprite_a_var.get())
        target_b = self._target_by_name(self.sprite_b_var.get())
        if not target_a or not target_b:
            messagebox.showwarning('Select sprites', 'Choose two sprites with costumes first.')
            return

        index_a = self.costume_a_combo.current()
        index_b = self.costume_b_combo.current()
        if index_a < 0 or index_b < 0:
            messagebox.showwarning('Select costumes', 'Choose a costume slot for both sprites.')
            return
        if target_a is target_b and index_a == index_b:
            messagebox.showwarning('Same costume selected', 'Choose two different costume slots to swap.')
            return

        name_a, name_b = swap_costume_artwork(target_a, index_a, target_b, index_b)
        self._mark_project_modified(
            f'Swapped artwork: {target_a["name"]} / {name_a} ↔ {target_b["name"]} / {name_b}'
        )

    def create_minigame(self):
        if not self.project_data:
            messagebox.showwarning('No project loaded', 'Load a .pmp project before creating a minigame.')
            return
        try:
            target, monitor, assets = build_clicker_minigame(self.project_data, self.minigame_name_var.get())
        except ValueError as exc:
            messagebox.showwarning('Cannot create minigame', str(exc))
            return
        self.project_data['targets'].append(target)
        self.project_data.setdefault('monitors', []).append(monitor)
        self.asset_updates.update(assets)
        self.minigame_name_var.set('')
        self._refresh_sprite_lists()
        self._mark_project_modified(f'Created playable minigame: {target["name"]}')

    def choose_html_file(self):
        path = filedialog.askopenfilename(
            title='Select AlphaClube HTML file',
            filetypes=[('HTML files', '*.html;*.htm'), ('All files', '*.*')],
            initialdir=str(self.html_path.parent if self.html_path.parent.exists() else APP_DIR),
        )
        if path:
            self.html_path = Path(path)
            self.html_path_var.set(str(self.html_path))
            self.config['last_html'] = str(self.html_path)
            save_config(CONFIG_FILE, self.config)

    def export_html_with_server(self):
        source = Path(self.html_path_var.get().strip())
        if not source.is_file():
            messagebox.showwarning('Choose HTML file', 'Select a valid AlphaClube HTML file first.')
            return
        try:
            server_url = validate_server_url(self.server_url_var.get())
        except ValueError as exc:
            messagebox.showwarning('Invalid server URL', str(exc))
            return

        output = filedialog.asksaveasfilename(
            title='Save HTML with the new server URL',
            defaultextension='.html',
            filetypes=[('HTML files', '*.html')],
            initialfile=f'{source.stem}_server_changed.html',
            initialdir=str(source.parent),
        )
        if not output:
            return
        output_path = Path(output)
        try:
            applied_url, replacements = patch_html_server_url(source, output_path, server_url)
        except (OSError, ValueError) as exc:
            messagebox.showerror('Homepage change failed', str(exc))
            return

        self.html_path = output_path.resolve()
        self.html_path_var.set(str(self.html_path))
        self.config['last_html'] = str(self.html_path)
        save_config(CONFIG_FILE, self.config)
        self.log(
            f'Wrote HTML server URL {applied_url} to {self.html_path.name}; '
            f'replaced {replacements} literal URL occurrence(s) and set window.ALPHACLUBE_SERVER.'
        )
        messagebox.showinfo('HTML updated', f'Saved patched HTML copy:\n{self.html_path}')

    def open_html_preview(self):
        html_path = Path(self.html_path_var.get().strip()) if hasattr(self, 'html_path_var') else self.html_path
        if not html_path.is_file():
            path = filedialog.askopenfilename(
                title='Select AlphaClube HTML preview',
                filetypes=[('HTML files', '*.html;*.htm'), ('All files', '*.*')],
                initialdir=str(APP_DIR),
            )
            if not path:
                return
            html_path = Path(path)

        self.html_path = html_path.resolve()
        self.config['last_html'] = str(self.html_path)
        save_config(CONFIG_FILE, self.config)
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        executable = str(pythonw) if pythonw.is_file() else sys.executable
        command = [
            executable,
            str(Path(__file__).resolve()),
            '--webview-preview',
            str(self.html_path),
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=str(APP_DIR),
                creationflags=0 if pythonw.is_file() else getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as exc:
            messagebox.showerror('Could not open preview', str(exc))
            return
        self.log(f'Started separate WebView preview (process {process.pid}): {self.html_path.name}')

    def _write_project_archive(self, output_path: Path):
        if not self.project_data or not self.project_path:
            raise ValueError('Load a .pmp project first.')
        output_path.parent.mkdir(parents=True, exist_ok=True)
        project_json = json.dumps(self.project_data, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        replacements = {'project.json': project_json}
        replacements.update(self.asset_updates)
        temp_path = output_path.with_name(f'{output_path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with zipfile.ZipFile(self.project_path, 'r') as source, zipfile.ZipFile(
                temp_path, 'w', compression=zipfile.ZIP_DEFLATED
            ) as output:
                output.comment = source.comment
                for info in source.infolist():
                    content = replacements.pop(info.filename, None)
                    if content is None:
                        content = source.read(info.filename)
                    output.writestr(info, content)
                for name, content in replacements.items():
                    output.writestr(name, content)
            temp_path.replace(output_path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def create_backup(self, show_message=True):
        if not self.project_path or not self.project_path.is_file():
            messagebox.showwarning('No project loaded', 'Load a .pmp project before creating a backup.')
            return None
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup = BACKUP_DIR / f'{self.project_path.stem}_backup_{timestamp}.pmp'
        shutil.copy2(self.project_path, backup)
        self.log(f'Created backup: {backup}')
        if show_message:
            messagebox.showinfo('Backup created', f'Backup saved to:\n{backup}')
        return backup

    def apply_project_changes(self):
        if not self.project_path or not self.project_data:
            messagebox.showwarning('No project loaded', 'Load a .pmp project before applying mods.')
            return
        if not self.project_modified:
            messagebox.showinfo('No pending changes', 'There are no staged PMP changes to apply.')
            return
        try:
            backup = self.create_backup(show_message=False)
            if backup is None:
                return
            self._write_project_archive(self.project_path)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            self.log(f'PMP patch failed: {exc}')
            messagebox.showerror('Could not apply mods', str(exc))
            return
        self.original_project_data = deepcopy(self.project_data)
        self.asset_updates = {}
        self.project_modified = False
        self.status_var.set(f'Updated {self.project_path.name}')
        self._update_project_view()
        self.log(f'Applied PMP changes. Backup: {backup.name}')
        messagebox.showinfo('PMP updated', f'Applied mods to:\n{self.project_path}\n\nBackup:\n{backup}')

    def export_project(self):
        if not self.project_path or not self.project_data:
            messagebox.showwarning('No project loaded', 'Load a .pmp project before exporting.')
            return
        output = filedialog.asksaveasfilename(
            title='Export modified AlphaClube project',
            defaultextension='.pmp',
            filetypes=[('PenguinMod project', '*.pmp'), ('ZIP archive', '*.zip')],
            initialfile=f'{self.project_path.stem}_modded.pmp',
        )
        if not output:
            return
        output_path = Path(output)
        try:
            self._write_project_archive(output_path)
        except (OSError, zipfile.BadZipFile, ValueError) as exc:
            self.log(f'PMP export failed: {exc}')
            messagebox.showerror('Export failed', str(exc))
            return
        self.log(f'Exported modified project: {output_path}')
        messagebox.showinfo('Export complete', f'Saved modified project to:\n{output_path}')

    def reset_project(self):
        if not self.project_path or self.original_project_data is None:
            messagebox.showwarning('No project loaded', 'Load a project before resetting staged changes.')
            return
        self.project_data = deepcopy(self.original_project_data)
        self.asset_updates = {}
        self.project_modified = False
        self._refresh_sprite_lists()
        self._update_project_view()
        self.status_var.set(f'Changes reset: {self.project_path.name}')
        self.log('Discarded staged PMP changes.')

    def open_backup_folder(self):
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == 'win32':
                subprocess.Popen(['explorer.exe', str(BACKUP_DIR)])
            else:
                raise OSError('Opening the backup folder is only configured for Windows.')
        except OSError as exc:
            messagebox.showerror('Could not open backup folder', str(exc))


def show_preview_error(message):
    error_root = tk.Tk()
    error_root.withdraw()
    messagebox.showerror('AlphaClube WebView preview failed', message, parent=error_root)
    error_root.destroy()


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--webview-preview':
        try:
            import webview
            html_path = Path(sys.argv[2]).resolve(strict=True)
            if not html_path.is_file():
                raise FileNotFoundError(f'HTML preview not found: {html_path}')
            webview.create_window(
                'AlphaClube HTML Preview',
                url=html_path.as_uri(),
                width=1200,
                height=800,
            )
            webview.start()
        except Exception as exc:
            show_preview_error(f'Could not start the WebView preview:\n{exc}')
        return

    root = tk.Tk()
    app = ClubePatcherApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
