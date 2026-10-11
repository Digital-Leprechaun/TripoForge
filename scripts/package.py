# Copyright (C) 2026 - Joseph M Wilcox
"""Package reusable code and starter projects; exclude all generated work/output."""
import argparse
from pathlib import Path
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='Destination ZIP file')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in ('README.md', 'LICENSE', 'COPYRIGHT.txt', 'TripoForge.ps1', 'TripoForge.cmd', 'config.json', '.gitignore')]
    for folder in ('docs', 'scripts', 'ExampleProjectFiles', 'web', 'Converters'):
        for path in (root / folder).rglob('*'):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if any(part.lower() in ('__pycache__', 'work', 'webwork', 'output', 'binaries', 'intermediate', 'saved') for part in parts):
                continue
            if path.name.endswith(('.pyc', '.local.json', '.tmp', '.blend', '.blend1', '.fbx', '.glb', '.uasset', '.umap')) or path.name in ('.forge.lock', 'model.download'):
                continue
            if 'Source' in parts and path.suffix.lower() != '.md':
                continue
            files.append(path)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root))
    print(f'Packaged {len(files)} files: {destination}')


if __name__ == '__main__':
    main()
