"""Build the small guard bundle deterministically; never packages private runtime files."""

import argparse
import base64
import hashlib
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def package(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
        for name in ("__init__.py", "native_inventory.py", "native_watchdog.py"):
            info = ZipInfo("controlplane/" + name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            data = (
                b""
                if name == "__init__.py"
                else (ROOT / "src/controlplane" / name).read_bytes().replace(b"\r\n", b"\n")
            )
            archive.writestr(info, data)
    return base64.b64encode(hashlib.sha256(target.read_bytes()).digest()).decode()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(package(parser.parse_args().output))
