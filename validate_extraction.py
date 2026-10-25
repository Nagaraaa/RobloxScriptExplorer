"""Validate Roblox RSB1 containers offline. Never executes recovered scripts.

Container format reference: tyronetheqt/Xeno, src/worker.cpp, decompress().
Requires Python 3.14+ for the standard-library Zstandard decoder.
"""
import argparse
import hashlib
import json
import struct
from collections import Counter
from compression import zstd
from pathlib import Path

MASK = 0xFFFFFFFF
P1, P2, P3, P4, P5 = 2654435761, 2246822519, 3266489917, 668265263, 374761393


def rol(value, count):
    value &= MASK
    return ((value << count) | (value >> (32 - count))) & MASK


def xxh32(data, seed=0):
    def round32(acc, value):
        return rol(acc + value * P2, 13) * P1 & MASK
    size, cursor = len(data), 0
    if size >= 16:
        acc = [(seed + P1 + P2) & MASK, (seed + P2) & MASK, seed & MASK, (seed - P1) & MASK]
        while cursor <= size - 16:
            words = struct.unpack_from('<4I', data, cursor)
            acc = [round32(a, word) for a, word in zip(acc, words)]
            cursor += 16
        value = sum(rol(a, shift) for a, shift in zip(acc, (1, 7, 12, 18))) & MASK
    else:
        value = (seed + P5) & MASK
    value = (value + size) & MASK
    while cursor <= size - 4:
        value = rol(value + struct.unpack_from('<I', data, cursor)[0] * P3, 17) * P4 & MASK
        cursor += 4
    while cursor < size:
        value = rol(value + data[cursor] * P5, 11) * P1 & MASK
        cursor += 1
    value ^= value >> 15
    value = value * P2 & MASK
    value ^= value >> 13
    value = value * P3 & MASK
    return value ^ (value >> 16)


def decode(raw):
    if not 16 <= len(raw) <= 16 * 1024 * 1024:
        raise ValueError('Container length outside bounds')
    key = bytes(((raw[i] ^ b'RSB1'[i]) - i * 41) & 255 for i in range(4))
    decoded = bytes(value ^ ((key[i % 4] + i * 41) & 255) for i, value in enumerate(raw))
    if xxh32(decoded, 42) != int.from_bytes(key, 'little'):
        raise ValueError('RSB checksum mismatch')
    expanded = int.from_bytes(decoded[4:8], 'little')
    if not 0 < expanded <= 32 * 1024 * 1024:
        raise ValueError('Expanded size outside bounds')
    decompressor = zstd.ZstdDecompressor()
    bytecode = decompressor.decompress(decoded[8:], max_length=expanded + 1)
    if len(bytecode) != expanded or not decompressor.eof or decompressor.unused_data:
        raise ValueError('Zstandard frame size or termination mismatch')
    if not bytecode or bytecode[0] == 0:
        raise ValueError('Empty bytecode or compiler-error payload')
    return bytecode


def validate_folder(folder):
    # Published XXH32 reference vectors, independent of the Roblox data.
    assert xxh32(b'') == 0x02CC5D05
    assert xxh32(b'hello') == 0xFB0077F9
    folder = Path(folder).resolve()
    manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf-8-sig'))
    output = folder / 'bytecode'
    output.mkdir(exist_ok=True)
    versions, failures, digests = Counter(), Counter(), set()
    validated = 0
    for finding in manifest['Findings']:
        if not finding.get('File'):
            continue
        try:
            filename = finding['File']
            if Path(filename).name != filename:
                raise ValueError('Invalid input filename')
            raw = (folder / filename).read_bytes()
            if hashlib.sha256(raw).hexdigest() != finding['SHA256']:
                raise ValueError('Snapshot SHA256 mismatch')
            bytecode = decode(raw)
            target = output / (Path(filename).stem + '.luac')
            target.write_bytes(bytecode)
            finding.update(Validation='RSB-checksum-and-Zstandard-valid',
                           BytecodeFile='bytecode/' + target.name,
                           BytecodeSize=len(bytecode), BytecodeVersion=bytecode[0],
                           BytecodeSHA256=hashlib.sha256(bytecode).hexdigest())
            validated += 1
            versions[bytecode[0]] += 1
            digests.add(finding['BytecodeSHA256'])
        except (ValueError, OSError, zstd.ZstdError) as exc:
            finding['ValidationError'] = str(exc)
            failures[str(exc)] += 1
    summary = dict(Validated=validated, UniqueBytecode=len(digests), Versions=dict(versions),
                   Failures=dict(failures), Extracted=len(manifest['Findings']),
                   ExecutionPerformed=False)
    manifest['ValidationSummary'] = summary
    (folder / 'validated-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    summary = validate_folder(args.directory)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary['Failures']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
