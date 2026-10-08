"""Validate repository links, generated proof bindings, diagram coverage and secret hygiene."""

import hashlib
import json
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def main():
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
                                    cwd=ROOT, text=True).splitlines()
    files = [ROOT / name for name in sorted(set(names)) if (ROOT/name).is_file()]
    errors, link_count = [], 0
    for path in files:
        if path.suffix == '.md':
            content = re.sub(r'```.*?```', '', path.read_text(encoding='utf-8'), flags=re.S)
            for link in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', content):
                target = link.split(' "')[0].strip('<>')
                parsed = urlsplit(target)
                if parsed.scheme or not parsed.path:
                    continue
                link_count += 1
                if not (path.parent / unquote(parsed.path)).resolve().exists():
                    errors.append(f'Broken link: {path.relative_to(ROOT)} -> {target}')
        if path.suffix in {'.py', '.tf', '.yml', '.md', '.json', '.pem'}:
            content = path.read_text(encoding='utf-8')
            patterns = [r'AKIA[A-Z0-9]{16}', r'ASIA[A-Z0-9]{16}', r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']
            if any(re.search(pattern, content) for pattern in patterns):
                errors.append(f'Credential material in {path.relative_to(ROOT)}')
    record = json.loads((ROOT/'docs/evidence/verification.json').read_text())
    for name, expected in record['source_sha256'].items():
        actual = hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest()
        if actual != expected:
            errors.append(f'Stale verification binding: {name}')
    diagrams = sorted((ROOT/'docs/diagrams').glob('*.mmd'))
    if len(diagrams) != 6 or any(not path.with_suffix('.svg').exists() for path in diagrams):
        errors.append('Six Mermaid sources and rendered SVGs are required')
    rendered = json.loads((ROOT/'docs/diagrams/manifest.json').read_text())
    for name, formats in rendered['files'].items():
        for extension, expected in formats.items():
            path = (ROOT/'docs/diagrams'/name).with_suffix('.'+extension)
            if hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
                errors.append(f'Stale rendered diagram: {path.name}')
    infrastructure = json.loads((ROOT/'docs/evidence/infrastructure/verification.json').read_text())
    for name, expected in infrastructure['source_sha256'].items():
        if hashlib.sha256((ROOT/name).read_bytes().replace(b'\r\n', b'\n')).hexdigest() != expected:
            errors.append(f'Stale infrastructure proof: {name}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(json.dumps({'markdown_links': link_count, 'source_bindings': len(record['source_sha256']),
                      'diagrams': len(diagrams), 'credential_patterns': 'clear'}))


if __name__ == '__main__':
    main()
