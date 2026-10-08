"""Render all maintainable Mermaid sources; a parser failure fails the command."""

import os
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
cli = ROOT / 'node_modules/@mermaid-js/mermaid-cli/src/cli.js'
records = {}
for source in sorted((ROOT / 'docs/diagrams').glob('*.mmd')):
    args = ['node', str(cli), '-i', str(source), '-o', str(source.with_suffix('.svg')),
            '-b', 'white', '-t', 'neutral']
    if os.environ.get('PUPPETEER_CONFIG'):
        args += ['-p', os.environ['PUPPETEER_CONFIG']]
    subprocess.run(args, check=True)
    records[source.name] = {p.suffix.removeprefix('.'): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                            for p in (source, source.with_suffix('.svg'))}
(ROOT/'docs/diagrams/manifest.json').write_text(json.dumps({'renderer': '@mermaid-js/mermaid-cli 11.12.0',
                                                          'files': records}, indent=2)+'\n')
print('Rendered six architecture diagrams' if len(list((ROOT / 'docs/diagrams').glob('*.mmd'))) == 6
      else 'Rendered architecture diagrams')
