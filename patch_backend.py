"""Apply original RSE corrections to the exact, independently downloaded backend.

The recipe contains line coordinates and our replacement code, not an upstream
source distribution. Both input and resulting normalized content are pinned.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def apply(root: Path, recipe_path: Path | None = None):
    root = root.resolve()
    recipe_path = recipe_path or Path(__file__).with_name('backend-fixes.json')
    recipe = json.loads(recipe_path.read_text(encoding='utf-8'))
    pending = []
    for item in recipe['files']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root):
            raise ValueError('Patch path escapes backend checkout')
        content = path.read_text(encoding='utf-8')
        actual = digest(content)
        if actual == item['patchedSHA256']:
            continue
        if actual != item['originalSHA256']:
            raise ValueError(f'Unsupported or modified backend file: {item["path"]}')
        lines = content.splitlines(keepends=True)
        for edit in reversed(item['edits']):
            lines[edit['startLine']:edit['endLine']] = edit['replacement']
        result = ''.join(lines)
        if digest(result) != item['patchedSHA256']:
            raise ValueError(f'Patch output digest mismatch: {item["path"]}')
        pending.append((path, result))
    for path, content in pending:
        path.write_text(content, encoding='utf-8', newline='\n')
    return len(pending)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('backend', type=Path)
    args = parser.parse_args()
    print(f'Applied pinned RSE recovery corrections to {apply(args.backend)} file(s).')
