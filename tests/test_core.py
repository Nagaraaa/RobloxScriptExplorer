import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from explorer_core import (Capture, Decompiler, ExplorerError, Script, analyze, contained,
                           export_capture, load_capture, resolve_dependencies)
from validate_extraction import decode, xxh32


def record(name='Main', path='Game/ReplicatedStorage/Main'):
    return Script('s0', name, path, 'ModuleScript', Path('unused.luac'), 'abc', 14, 3)


class CaptureTests(unittest.TestCase):
    def test_paths_cannot_escape_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(contained(root, 'bytecode/a.luac'), root / 'bytecode/a.luac')
            for bad in ('../outside.luac', str(root.parent / 'outside.luac')):
                with self.assertRaises(ExplorerError):
                    contained(root, bad)

    def test_manifest_checks_digest_and_preserves_duplicate_instances(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = b'\x0e\x03synthetic'
            (root / 'a.luac').write_bytes(raw)
            entries = [dict(Name='Main', Path=f'Game/Folder{i}/Main', BytecodeFile='a.luac',
                            BytecodeSHA256=hashlib.sha256(raw).hexdigest()) for i in range(2)]
            entries.append(dict(Name='Corrupted', BytecodeFile='a.luac', BytecodeSHA256='0'*64))
            entries.append(dict(Name='Escape', BytecodeFile='../outside.luac'))
            (root / 'manifest.json').write_text(json.dumps({'Findings': entries}), encoding='utf-8')
            capture = load_capture(root)
            self.assertEqual(len(capture.scripts), 2)
            self.assertEqual(len(capture.warnings), 2)
            self.assertNotEqual(capture.scripts[0].id, capture.scripts[1].id)

    def test_export_sanitizes_names_and_does_not_overwrite(self):
        item = record('../CON:Main')
        item.source = 'return 1'
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'export'
            report = export_capture(Capture(Path(directory), [item]), destination)
            self.assertTrue(contained(destination, report['scripts'][0]['file']).is_file())
            self.assertFalse(report['executionPerformed'])
            with self.assertRaises(ExplorerError):
                export_capture(Capture(Path(directory), [item]), destination)


class AnalysisTests(unittest.TestCase):
    def test_comments_and_strings_do_not_invent_calls(self):
        source = '''-- require(script.Parent.Fake)
--[=[ game:GetService("Fake") ]=]
local text = "require(script.Fake) :FireServer()"
local rs = game:GetService("ReplicatedStorage")
local dep = require(rs:WaitForChild("Network"))
local function hello() remote:FireServer(1) end
'''
        data = analyze(source)
        self.assertEqual(data['services'], ['ReplicatedStorage'])
        self.assertEqual(data['aliases'], {'rs': 'ReplicatedStorage'})
        self.assertEqual([row['expression'] for row in data['requires']], ['rs:WaitForChild("Network")'])
        self.assertEqual([row['method'] for row in data['remotes']], ['FireServer'])
        self.assertEqual(data['functions'][0]['name'], 'hello')

    def test_literal_dependency_paths_resolve_and_dynamic_calls_stay_unresolved(self):
        main = record()
        other = record('Network', 'Game/ReplicatedStorage/Network')
        other.id = 's1'
        main.analysis = analyze('local rs = game:GetService("ReplicatedStorage")\n'
                                'require(script.Parent.Network)\nrequire(rs:WaitForChild("Network"))\nrequire(computePath())')
        deps = resolve_dependencies(main, Capture(Path('.'), [main, other]))
        self.assertEqual(deps[0]['ids'], ['s1'])
        self.assertEqual(deps[1]['ids'], ['s1'])
        self.assertEqual(deps[2]['ids'], [])


class ContainerTests(unittest.TestCase):
    def test_reference_checksum_vectors(self):
        self.assertEqual(xxh32(b''), 0x02CC5D05)
        self.assertEqual(xxh32(b'hello'), 0xFB0077F9)

    def test_container_roundtrip_and_corruption_rejection(self):
        from compression import zstd
        raw = b'\x0e\x03' + b'our own synthetic bytes' * 10
        decoded = b'RSB1' + len(raw).to_bytes(4, 'little') + zstd.compress(raw)
        key = xxh32(decoded, 42).to_bytes(4, 'little')
        packed = bytes(value ^ ((key[i % 4] + 41*i) & 255) for i, value in enumerate(decoded))
        self.assertEqual(decode(packed), raw)
        with self.assertRaises(ValueError):
            decode(packed[:-1] + bytes([packed[-1] ^ 1]))
        with self.assertRaises(ValueError):
            decode(packed[:7])


class DecompilerTests(unittest.TestCase):
    def test_cache_is_keyed_by_engine_and_encoding_and_rechecks_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine_file = root / 'engine.exe'
            engine_file.write_bytes(b'synthetic engine')
            bytecode = root / 'input.luac'
            bytecode.write_bytes(b'\x0e\x03data')
            capture = load_capture(bytecode)
            engine = Decompiler(engine_file, root / 'cache')
            with patch.object(engine, '_run', return_value='return 1') as run:
                engine.run(capture.scripts[0])
                engine.run(capture.scripts[0])
                engine.run(capture.scripts[0], encoding='standard')
                self.assertEqual(run.call_count, 2)
                bytecode.write_bytes(b'\x0e\x03changed')
                with self.assertRaises(ExplorerError):
                    engine.run(capture.scripts[0])

    @unittest.skipUnless(os.environ.get('RSE_RUN_INTEGRATION') == '1', 'native backend opt-in')
    def test_native_decompile_json_disassembly_and_syntax(self):
        root = Path(__file__).resolve().parents[1]
        engine = Decompiler(root / 'tools/luaudec.exe', root / '.local/test-cache', root / 'tools/luau-compile.exe')
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / 'sample.luau'
            sample.write_text('local function sum(a,b) return a+b end\nreturn sum(20,22)', encoding='utf-8')
            compiler = root / 'tools/luau-compile.exe'
            compiled = subprocess.run([str(compiler), '--binary', str(sample)], capture_output=True, check=True)
            bytecode = sample.with_suffix('.luac')
            bytecode.write_bytes(compiled.stdout)
            item = load_capture(bytecode).scripts[0]
            engine.decompile(item, 'standard')
            self.assertNotEqual(item.status, 'Échec', item.diagnostics)
            self.assertEqual(item.syntax, 'Syntaxe vérifiée')
            self.assertIn('return', item.source)
            self.assertIn('RETURN', engine.run(item, 'disasm', 'standard'))
            self.assertTrue(engine.structure(item, 'standard')['protos'])
            restored = load_capture(bytecode).scripts[0]
            self.assertTrue(engine.restore_cached(restored, 'standard'))
            self.assertEqual(restored.syntax, 'Syntaxe vérifiée')


if __name__ == '__main__':
    unittest.main()
