"""Local/CI infrastructure checks; Terraform uses a mock provider and never deploys AWS resources."""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--terraform', default='terraform')
    parser.add_argument('--trivy', default='trivy')
    parser.add_argument('--output', type=Path, default=ROOT/'work/infra-proof')
    args = parser.parse_args()
    trivy_version = subprocess.check_output([args.trivy, '--version'], text=True).splitlines()[0]
    if trivy_version.strip() != 'Version: 0.75.0':
        raise RuntimeError('Use the pinned Trivy 0.75.0 scanner')
    args.output.mkdir(parents=True, exist_ok=True)
    commands = [[args.terraform, 'fmt', '-check', '-recursive', 'infra']]
    for module in ('foundation', 'aws'):
        for options in [['init', '-backend=false', '-input=false', '-lockfile=readonly'], ['validate', '-no-color']]:
            commands.append([args.terraform, f'-chdir=infra/{module}', *options])
    commands.append([args.terraform, '-chdir=infra/aws', 'test', '-no-color'])
    commands.append([args.trivy, 'config', '--severity', 'HIGH,CRITICAL', '--exit-code', '1',
                     '--tf-vars', 'infra/testing/static.tfvars.json',
                     '--format', 'json', '--output', str(args.output/'security.json'), 'infra'])
    commands.append([args.trivy, 'fs', '--scanners', 'secret', '--exit-code', '1',
                     '--skip-dirs', 'node_modules', '--skip-dirs', 'work', '--skip-dirs', '.git',
                     '--skip-dirs', 'infra/aws/.terraform', '--skip-dirs', 'infra/foundation/.terraform',
                     '--format', 'json', '--output', str(args.output/'secrets.json'), '.'])
    results = []
    for i, command in enumerate(commands):
        process = subprocess.run(command, cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        (args.output/f'{i:02}-check.log').write_text(process.stdout.rstrip() + ('\n' if process.stdout else ''),
                                                 encoding='utf-8', newline='\n')
        print(process.stdout)
        process.check_returncode()
        results.append({'command': [Path(command[0]).name, *command[1:]], 'exit_code': process.returncode})
    files = sorted(p for p in (ROOT/'infra').rglob('*') if p.is_file() and '.terraform' not in p.parts)
    result = {'executed_at': datetime.now(UTC).isoformat(), 'scope': 'static analysis and mocked Terraform; no AWS API calls',
              'terraform_version': '1.13.5', 'trivy_version': '0.75.0', 'checks': results,
              'source_sha256': {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
                                for p in files}}
    (args.output/'verification.json').write_text(json.dumps(result, indent=2)+'\n')


if __name__ == '__main__':
    main()
