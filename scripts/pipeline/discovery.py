# Copyright (C) 2026 - Joseph M Wilcox
"""Deterministic filename grouping; never guess view order from directory order."""
from pathlib import Path
import re

from .common import PipelineError

VIEWS = ('front', 'left', 'back', 'right')
IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg'}
SEPARATOR = r'[ _.-]+'


def discover(folder, single_image=False, default_name='Model'):
    folder = Path(folder).resolve()
    if not folder.is_dir():
        raise PipelineError(f'Input folder not found: {folder}')
    images = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS),
                    key=lambda p: (p.name.casefold(), p.name))
    if not images:
        raise PipelineError(f'No PNG/JPEG images found in {folder}')
    groups = {}
    for image in images:
        stem = image.stem
        if single_image:
            name, view = stem, 'image'
        elif stem.casefold() in VIEWS:
            name, view = default_name, stem.casefold()
        else:
            suffix = re.fullmatch(r'(.+?)' + SEPARATOR + r'(front|left|back|right)', stem, re.IGNORECASE)
            prefix = re.fullmatch(r'(front|left|back|right)' + SEPARATOR + r'(.+)', stem, re.IGNORECASE)
            if suffix and prefix:
                raise PipelineError(f'Ambiguous view labels: {image.name}')
            if suffix:
                name, view = suffix.group(1), suffix.group(2).lower()
            elif prefix:
                view, name = prefix.group(1).lower(), prefix.group(2)
            else:
                raise PipelineError(f'Cannot identify model/view in {image.name}. Use model_front/left/back/right filenames or --singleimage.')
        # Names become output files/directories, so reject Windows device names too.
        if name in ('.', '..') or name.endswith((' ', '.')) or re.search(r'[<>:"/\\|?*\x00-\x1f]', name):
            raise PipelineError(f'Invalid output model name: {name!r}')
        if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.IGNORECASE):
            raise PipelineError(f'Reserved output model name: {name}')
        key = name.casefold()
        group = groups.setdefault(key, {'name': name, 'inputs': {}})
        if view in group['inputs']:
            raise PipelineError(f'Duplicate {view} image for {group["name"]}: {Path(group["inputs"][view]).name}, {image.name}')
        group['inputs'][view] = str(image)
    for group in groups.values():
        if not single_image:
            missing = set(VIEWS) - group['inputs'].keys()
            if missing:
                raise PipelineError(f'{group["name"]}: missing views: {", ".join(v for v in VIEWS if v in missing)}')
    return sorted(groups.values(), key=lambda g: g['name'].casefold())
