"""Local capture/decompiler integration. Recovered scripts are data, never code to run."""
from __future__ import annotations

import hashlib
from bisect import bisect_right
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MAX_INPUT = 32 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
VALIDATION_REVISION = 'recovery-v2'


class ExplorerError(Exception):
    pass


def contained(root: Path, relative: str) -> Path:
    """Capture metadata must not redirect reads outside the selected capture."""
    if not isinstance(relative, str) or not relative:
        raise ExplorerError('Chemin de fichier absent du manifeste.')
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ExplorerError('Chemin hors du dossier de capture refusé.')
    return result


def read_bounded(path: Path, limit: int = MAX_INPUT) -> bytes:
    with path.open('rb') as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise ExplorerError(f'Fichier trop volumineux : {path.name}')
    return content


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic replace prevents partial cache files after interruption.
    with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as out:
        json.dump(value, out, ensure_ascii=False, indent=2)
        temp = Path(out.name)
    try:
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (attempt + 1))
    finally:
        temp.unlink(missing_ok=True)


@dataclass
class Script:
    id: str
    name: str
    path: str
    kind: str
    file: Path
    digest: str
    version: int
    size: int
    source: str = ''
    status: str = 'À décompiler'
    diagnostics: list[str] = field(default_factory=list)
    analysis: dict = field(default_factory=dict)
    structural: dict = field(default_factory=dict)
    syntax: str = 'Non vérifiée'


@dataclass
class Capture:
    root: Path
    scripts: list[Script]
    build: str = 'Import local'
    warnings: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    _paths: dict = field(default_factory=dict, repr=False)
    _indexed_count: int = field(default=-1, repr=False)

    def path_index(self):
        if self._indexed_count != len(self.scripts):
            self._paths = {}
            for item in self.scripts:
                self._paths.setdefault(item.path, []).append(item.id)
            self._indexed_count = len(self.scripts)
        return self._paths


def load_capture(selected: Path) -> Capture:
    selected = selected.resolve()
    if selected.is_dir():
        manifest = next((selected / name for name in ('validated-manifest.json', 'manifest.json')
                         if (selected / name).is_file()), None)
        if manifest is None:
            raise ExplorerError('Le dossier ne contient pas manifest.json ou validated-manifest.json.')
    else:
        manifest = selected
    if manifest.suffix.lower() in ('.luac', '.luauc'):
        data = read_bounded(manifest)
        return Capture(manifest.parent, [_make_script(0, manifest.stem, manifest.name, 'Bytecode', manifest, data)])
    try:
        document = json.loads(read_bounded(manifest, 16 * 1024 * 1024).decode('utf-8-sig'))
    except (ValueError, UnicodeError, OSError) as exc:
        raise ExplorerError(f'Manifeste illisible : {exc}') from exc
    entries = document.get('Findings')
    if not isinstance(entries, list) or len(entries) > 30000:
        raise ExplorerError('Liste de scripts absente ou trop volumineuse.')
    result = Capture(manifest.parent, [], str(document.get('Build', 'Inconnue')),
                     scope=list(document.get('Scope', [])))
    if document.get('Limited'):
        result.warnings.append('Le parcours de capture a atteint une limite : capture partielle.')
    if document.get('ReadFailures'):
        result.warnings.append(f"{document['ReadFailures']} lectures ont échoué pendant le parcours.")
    for index, entry in enumerate(entries):
        try:
            if not isinstance(entry, dict):
                raise ExplorerError('Entrée invalide.')
            relative = entry.get('BytecodeFile')
            if not relative and entry.get('File'):
                relative = 'bytecode/' + Path(entry['File']).stem + '.luac'
            if not relative:
                raise ExplorerError(entry.get('Error') or 'Bytecode absent.')
            path = contained(result.root, relative)
            data = read_bounded(path)
            expected = entry.get('BytecodeSHA256')
            if expected and hashlib.sha256(data).hexdigest() != expected:
                raise ExplorerError('Empreinte SHA-256 différente du manifeste.')
            result.scripts.append(_make_script(index, str(entry.get('Name', path.stem)),
                                              str(entry.get('Path') or path.name),
                                              str(entry.get('Class', 'Bytecode')), path, data))
        except (ExplorerError, OSError, TypeError, ValueError) as exc:
            result.warnings.append(f'Entrée {index + 1} ignorée : {exc}')
    if not result.scripts:
        raise ExplorerError('Aucun bytecode valide trouvé. ' + '\n'.join(result.warnings[:3]))
    return result


def _make_script(index, name, instance_path, kind, path, data):
    if not data or not 3 <= data[0] <= 14:
        raise ExplorerError('Version de bytecode hors de la plage 3–14.')
    digest = hashlib.sha256(data).hexdigest()
    return Script(f's{index}', name, instance_path, kind, path, digest, data[0], len(data))


# A small lexical scanner avoids reporting APIs hidden in comments or string literals.
TOKEN = re.compile(r'--\[(=*)\[.*?\]\1\]|--[^\n]*|\[(=*)\[.*?\]\2\]|'
                   r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|'
                   r'[A-Za-z_]\w*|\d+(?:\.\d+)?|\S', re.S)


def tokens(source: str):
    result = []
    for match in TOKEN.finditer(source):
        text = match.group()
        if text.startswith('--'):
            continue
        if text[:1] in ('"', "'"):
            kind = 'string'
            value = text[1:-1]
        elif text.startswith('[') and re.match(r'^\[=*\[', text):
            kind = 'string'
            delim = re.match(r'^\[=*\[', text).group()
            value = text[len(delim):-len(delim)]
        else:
            kind, value = 'code', text
        result.append((kind, value, match.start(), match.end()))
    return result


def collect_diagnostics(source: str) -> list[str]:
    """Only engine-authored comment markers count, never words in game strings."""
    return [match.group().strip() for match in TOKEN.finditer(source)
            if re.match(r'^--\s*luaudec:', match.group())]


def analyze(source: str) -> dict:
    parts = tokens(source)
    newlines = [match.start() for match in re.finditer('\n', source)]
    services, requires, remotes, functions, strings, aliases = [], [], [], [], [], {}
    for i, (kind, value, start, end) in enumerate(parts):
        if kind == 'string':
            strings.append(value)
            continue
        previous = parts[i - 1][1] if i else ''
        following = parts[i + 1][1] if i + 1 < len(parts) else ''
        line = bisect_right(newlines, start) + 1
        if value == 'GetService' and previous == ':' and following == '(' and i + 2 < len(parts) and parts[i + 2][0] == 'string':
            service = parts[i + 2][1]
            services.append(service)
            # local alias = game:GetService("Service")
            if i >= 5 and parts[i - 3][1] == '=' and parts[i - 4][0] == 'code':
                aliases[parts[i - 4][1]] = service
        if value == 'require' and following == '(' and previous not in ('.', ':'):
            depth, close = 1, i + 2
            while close < len(parts) and close < i + 128:
                if parts[close][0] == 'code':
                    depth += (parts[close][1] == '(') - (parts[close][1] == ')')
                if depth == 0:
                    break
                close += 1
            if depth == 0:
                requires.append({'expression': source[parts[i + 1][3]:parts[close][2]].strip(), 'line': line})
        if value in ('FireServer', 'InvokeServer', 'FireClient', 'FireAllClients', 'OnClientEvent', 'OnClientInvoke') and previous in (':', '.'):
            remotes.append({'method': value, 'line': line})
        if value == 'function':
            labels, cursor = [], i + 1
            while cursor < len(parts) and parts[cursor][1] != '(' and len(labels) < 10:
                labels.append(parts[cursor][1])
                cursor += 1
            functions.append({'name': ''.join(labels) or '(anonyme)', 'line': line})
    return {'services': sorted(set(services)), 'requires': requires, 'remotes': remotes,
            'functions': functions, 'strings': sorted(set(strings)), 'aliases': aliases,
            'lines': source.count('\n') + 1}


def resolve_dependencies(script: Script, capture: Capture) -> list[dict]:
    """Resolve only simple literal paths; dynamic requires stay unresolved."""
    if not script.analysis.get('requires'):
        return []
    paths = capture.path_index()
    resolved = []
    for require in script.analysis.get('requires', []):
        expression = require['expression']
        parts = tokens(expression)
        names = [part[1] for part in parts]
        root = script.path.split('/')[0]
        cursor, path = 1, []
        if names and names[0] == 'script':
            path = script.path.split('/')
        elif names and names[0] in script.analysis.get('aliases', {}):
            path = [root, script.analysis['aliases'][names[0]]]
        elif names and names[0] == 'game':
            path = [root]
        else:
            cursor = len(parts) + 1
        while path and cursor < len(parts):
            if names[cursor] == '.' and cursor + 1 < len(parts):
                field = names[cursor + 1]
                if field == 'Parent':
                    path.pop()
                else:
                    path.append(field)
                cursor += 2
            elif (names[cursor] == ':' and cursor + 4 < len(parts)
                  and names[cursor + 1] in ('WaitForChild', 'FindFirstChild', 'GetService')
                  and names[cursor + 2] == '(' and parts[cursor + 3][0] == 'string'
                  and names[cursor + 4] == ')'):
                path.append(names[cursor + 3])
                cursor += 5
            elif (names[cursor] == '[' and cursor + 2 < len(parts)
                  and parts[cursor + 1][0] == 'string' and names[cursor + 2] == ']'):
                path.append(names[cursor + 1])
                cursor += 3
            else:
                path = []
                break
        target = '/'.join(path) if cursor == len(parts) else ''
        ids = paths.get(target, [])
        resolved.append(dict(require, target=target, ids=ids,
                             resolution='Chemin statique' if ids else 'Non résolu / dynamique'))
    return resolved


class Decompiler:
    def __init__(self, executable: Path, cache: Path, compiler: Path | None = None):
        self.executable = executable.resolve()
        if not self.executable.is_file():
            raise ExplorerError('Décompilateur absent. Lancez Build-Backend.ps1 ou choisissez luaudec.exe.')
        self.identity = hashlib.sha256(read_bounded(self.executable)).hexdigest()
        self.cache = cache.resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.compiler = compiler if compiler and compiler.is_file() else None
        self.compiler_identity = hashlib.sha256(read_bounded(self.compiler)).hexdigest() if self.compiler else 'none'
        self._locks_guard = threading.Lock()
        self._content_locks = {}

    def _content_lock(self, script, encoding):
        with self._locks_guard:
            return self._content_locks.setdefault((script.digest, encoding), threading.RLock())

    def _run(self, file: Path, mode: str, encoding: str = 'roblox') -> str:
        data = read_bounded(file)
        if not data or not 3 <= data[0] <= 14:
            raise ExplorerError('Version de bytecode non prise en charge.')
        arguments = [str(self.executable)]
        if encoding != 'auto':
            arguments.append('--' + encoding)
        arguments.extend({'source': ['-v'], 'disasm': ['--disasm'], 'json': ['--json'], 'info': ['--info']}[mode])
        # The executable reads only a private copy of the selected bytes. It never
        # interprets the recovered Luau, and input names cannot become CLI options.
        with tempfile.TemporaryDirectory(prefix='rse-') as directory:
            snapshot = Path(directory) / 'input.luac'
            snapshot.write_bytes(data)
            arguments.append(str(snapshot))
            out_path, err_path = Path(directory) / 'stdout.txt', Path(directory) / 'stderr.txt'
            try:
                with out_path.open('wb') as stdout, err_path.open('wb') as stderr:
                    process = subprocess.run(arguments, stdout=stdout, stderr=stderr, timeout=12,
                                             creationflags=CREATE_NO_WINDOW, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ExplorerError('Délai de décompilation dépassé (12 s).') from exc
            error = read_bounded(err_path, MAX_OUTPUT).decode('utf-8', 'replace')
            output = read_bounded(out_path, MAX_OUTPUT).decode('utf-8', 'replace')
        if process.returncode:
            raise ExplorerError(error.strip() or f'Décompilateur arrêté : {process.returncode}')
        return output

    def cache_path(self, script: Script, mode: str, encoding: str) -> Path:
        return self.cache / self.identity / encoding / (script.digest + '.' + mode + '.json')

    def run(self, script: Script, mode='source', encoding='roblox') -> str:
        with self._content_lock(script, encoding):
            return self._run_cached(script, mode, encoding)

    def _run_cached(self, script: Script, mode='source', encoding='roblox') -> str:
        if mode not in ('source', 'disasm', 'json', 'info') or encoding not in ('roblox', 'standard', 'auto'):
            raise ExplorerError('Mode de décompilation invalide.')
        # Verify on each use, including cache hits, to avoid mixing modified captures.
        if hashlib.sha256(read_bounded(script.file)).hexdigest() != script.digest:
            raise ExplorerError('Le fichier de bytecode a changé depuis son ouverture.')
        path = self.cache_path(script, mode, encoding)
        if path.exists():
            try:
                return json.loads(read_bounded(path, MAX_OUTPUT).decode('utf-8'))['output']
            except (ValueError, KeyError, TypeError):
                pass
        output = self._run(script.file, mode, encoding)
        write_json(path, {'engine': self.identity, 'bytecode': script.digest, 'output': output})
        return output

    def cached_source(self, script: Script, encoding='roblox') -> str | None:
        path = self.cache_path(script, 'source', encoding)
        if not path.is_file():
            return None
        try:
            return json.loads(read_bounded(path, MAX_OUTPUT).decode('utf-8'))['output']
        except (ValueError, KeyError, OSError):
            return None

    def restore_cached(self, script: Script, encoding='roblox') -> bool:
        with self._content_lock(script, encoding):
            return self._restore_cached(script, encoding)

    def _restore_cached(self, script: Script, encoding='roblox') -> bool:
        source = self.cached_source(script, encoding)
        if not source:
            return False
        script.source, script.analysis = source, analyze(source)
        script.diagnostics = collect_diagnostics(source)
        script.status = 'Partiel' if script.diagnostics else 'Reconstruit'
        validation = self.cache_path(script, 'validation-' + VALIDATION_REVISION + '-' + self.compiler_identity, encoding)
        if validation.is_file():
            try:
                saved = json.loads(read_bounded(validation).decode('utf-8'))
                if saved['sourceSHA256'] == hashlib.sha256(source.encode('utf-8')).hexdigest():
                    script.syntax, script.status = saved['syntax'], saved['status']
            except (OSError, ValueError, KeyError):
                pass
        return True

    def decompile(self, script: Script, encoding='roblox') -> Script:
        with self._content_lock(script, encoding):
            return self._decompile_locked(script, encoding)

    def _decompile_locked(self, script: Script, encoding='roblox') -> Script:
        try:
            source = self.run(script, 'source', encoding)
            if not source.strip():
                raise ExplorerError('Le décompilateur a produit une sortie vide.')
            self.restore_cached(script, encoding)
            # This is a syntax check, never a run of the recovered program.
            if self.compiler and script.syntax == 'Non vérifiée':
                script.syntax = self.check_syntax(source)
                if script.syntax != 'Syntaxe vérifiée':
                    script.status = 'Partiel'
            write_json(self.cache_path(script, 'validation-' + VALIDATION_REVISION + '-' + self.compiler_identity, encoding),
                       {'sourceSHA256': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                        'syntax': script.syntax, 'status': script.status})
        except (ExplorerError, OSError) as exc:
            script.status = 'Échec'
            script.diagnostics = [str(exc)]
        return script

    def check_syntax(self, source: str) -> str:
        with tempfile.TemporaryDirectory(prefix='rse-syntax-') as directory:
            path = Path(directory) / 'recovered.luau'
            path.write_text(source, encoding='utf-8')
            try:
                process = subprocess.run([str(self.compiler), '--null', str(path)], capture_output=True,
                                         timeout=12, creationflags=CREATE_NO_WINDOW, check=False)
            except subprocess.TimeoutExpired:
                return 'Vérification de syntaxe expirée'
        if process.returncode == 0:
            return 'Syntaxe vérifiée'
        return 'Syntaxe à revoir : ' + process.stderr.decode('utf-8', 'replace')[:1500]

    def structure(self, script: Script, encoding='roblox') -> dict:
        try:
            data = json.loads(self.run(script, 'json', encoding))
        except ValueError as exc:
            raise ExplorerError('Sortie structurelle JSON invalide.') from exc
        script.structural = data
        return data


def export_capture(capture: Capture, destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists():
        raise ExplorerError('Choisissez un nouveau dossier pour préserver les exports existants.')
    destination.mkdir(parents=True)
    scripts_dir = destination / 'scripts'
    scripts_dir.mkdir()
    rows = []
    for script in capture.scripts:
        filename = None
        if script.source:
            safe = re.sub(r'[^\w.-]', '_', script.name)[:70] or 'script'
            filename = f'scripts/{script.id}_{safe}.luau'
            (destination / filename).write_text(script.source, encoding='utf-8')
        rows.append({'name': script.name, 'path': script.path, 'status': script.status,
                     'sha256': script.digest, 'file': filename, 'syntax': script.syntax,
                     'diagnostics': script.diagnostics, 'analysis': script.analysis,
                     'dependencies': resolve_dependencies(script, capture)})
    report = {'build': capture.build, 'scope': capture.scope, 'warnings': capture.warnings,
              'executionPerformed': False, 'scripts': rows}
    write_json(destination / 'analysis.json', report)
    return report


def capture_live(app_root: Path, destination: Path, log: Callable[[str], None] | None = None) -> Path:
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        raise ExplorerError('PowerShell est introuvable.')
    command = [shell, '-NoProfile', '-File', str(app_root / 'Extract-RobloxClient.ps1'),
               '-OutputDirectory', str(destination.resolve()), '-SkipValidation']
    try:
        result = subprocess.run(command, capture_output=True, timeout=90,
                                creationflags=CREATE_NO_WINDOW, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ExplorerError('La capture a dépassé 90 secondes.') from exc
    if result.returncode:
        raise ExplorerError(result.stderr.decode('utf-8', 'replace')[-4000:])
    # Call validation in this Python process, including in frozen GUI builds.
    from validate_extraction import validate_folder
    summary = validate_folder(destination)
    if summary['Failures']:
        raise ExplorerError('Certains conteneurs de la capture ne sont pas valides. Consultez le manifeste.')
    if log:
        log(f"{summary['Validated']} conteneurs validés")
    return destination
