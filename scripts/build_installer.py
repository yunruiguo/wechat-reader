"""Build a download-and-unzip installer from reviewed Git-tracked files only."""

from pathlib import Path
import subprocess
import tomllib
from zipfile import ZipFile, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
destination = root / 'dist' / f'wechat-reader-setup-v{version}.zip'
destination.parent.mkdir(exist_ok=True)
files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
with ZipFile(destination, 'w', compression=ZIP_DEFLATED) as archive:
    for name in filter(None, files):
        archive.write(root / name, f'wechat-reader-v{version}/{name}')
print(destination)
