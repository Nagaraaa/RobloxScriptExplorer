"""Recovery regression tests use only original synthetic programs, never game dumps."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from explorer_core import Decompiler, collect_diagnostics, load_capture
from patch_backend import apply


class DiagnosticTests(unittest.TestCase):
    def test_only_engine_comments_are_diagnostics(self):
        source = '''warn("unsupported feature")
local marker = "-- luaudec: this is game data"
-- game comment about an unsupported setting
-- luaudec: actual reconstruction warning
'''
        self.assertEqual(collect_diagnostics(source), ['-- luaudec: actual reconstruction warning'])

    def test_parallel_duplicate_contents_share_cache_safely(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            native = folder / 'engine.exe'
            native.write_bytes(b'synthetic engine identity')
            bytecode = folder / 'test.luac'
            bytecode.write_bytes(b'\x0e\x03synthetic')
            engine = Decompiler(native, folder / 'cache')
            items = [load_capture(bytecode).scripts[0] for _ in range(24)]
            with patch.object(engine, '_run', return_value='return 42') as run:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    results = list(pool.map(engine.decompile, items))
                self.assertEqual(run.call_count, 1)
            self.assertEqual({item.status for item in results}, {'Reconstruit'})


class PatchTests(unittest.TestCase):
    def test_patch_is_pinned_and_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / 'file.cpp'
            before, after = 'first\nold\nlast\n', 'first\nnew\nlast\n'
            sha = lambda s: hashlib.sha256(s.encode()).hexdigest()
            recipe = root / 'recipe.json'
            recipe.write_text(json.dumps({'files': [{'path': 'file.cpp', 'originalSHA256': sha(before),
                               'patchedSHA256': sha(after), 'edits': [{'startLine': 1, 'endLine': 2,
                                                                    'replacement': ['new\n']}]}]}), encoding='utf-8')
            path.write_text(before, encoding='utf-8')
            self.assertEqual(apply(root, recipe), 1)
            self.assertEqual(path.read_text(encoding='utf-8'), after)
            self.assertEqual(apply(root, recipe), 0)
            path.write_text('unrecognized content', encoding='utf-8')
            with self.assertRaises(ValueError):
                apply(root, recipe)
            self.assertEqual(path.read_text(encoding='utf-8'), 'unrecognized content')


@unittest.skipUnless(os.environ.get('RSE_RUN_INTEGRATION') == '1', 'native backend opt-in')
class SemanticRecoveryTests(unittest.TestCase):
    def test_synthetic_programs_roundtrip_at_all_optimization_levels(self):
        compiler = ROOT / 'tools/luau-compile.exe'
        repl = ROOT / 'tools/luau.exe'
        engine = Decompiler(ROOT / 'tools/luaudec.exe', ROOT / '.local/recovery-test-cache', compiler)
        fixtures = json.loads((ROOT / 'tests/fixtures/recovery_cases.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name, body in fixtures.items():
                source = ('local function f(a,b)\n' + body + '\nend\n'
                          'for a=-2,5 do for b=-1,6 do print(a,b,f(a,b)) end end\n')
                original = folder / 'original.luau'
                original.write_text(source, encoding='utf-8')
                expected = subprocess.run([str(repl), str(original)], capture_output=True, timeout=10, check=True).stdout
                for optimization in (0, 1, 2):
                    with self.subTest(fixture=name, optimization=optimization):
                        compiled = subprocess.run([str(compiler), '--binary', f'-O{optimization}', str(original)],
                                                  capture_output=True, timeout=10, check=True).stdout
                        path = folder / 'input.luac'
                        path.write_bytes(compiled)
                        item = load_capture(path).scripts[0]
                        engine.decompile(item, 'standard')
                        self.assertEqual(item.syntax, 'Syntaxe vérifiée', item.diagnostics)
                        recovered = folder / 'recovered.luau'
                        recovered.write_text(item.source, encoding='utf-8')
                        actual = subprocess.run([str(repl), str(recovered)], capture_output=True, timeout=10)
                        self.assertEqual(actual.returncode, 0, actual.stderr.decode('utf-8', 'replace'))
                        self.assertEqual(actual.stdout, expected)


if __name__ == '__main__':
    unittest.main()
