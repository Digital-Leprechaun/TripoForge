# Copyright (C) 2026 - Joseph M Wilcox
"""Project launcher: shared settings, isolated jobs, and final project output."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import sys
import uuid

from pipeline import config
from pipeline.common import PipelineError, read_json, sha256, write_json
from pipeline.runner import Build, ROOT
from pipeline.tripo import TripoClient
from pipeline.discovery import discover
from pipeline.pricing import show_cost


def show_balance(settings_file, project_file=None):
    if not Path(settings_file).is_file():
        raise PipelineError(f'Settings not found: {settings_file}')
    cfg = config.load_settings(settings_file)
    if project_file:
        cfg = config.load(project_file, defaults=cfg)
    data = TripoClient.from_config(cfg).balance()
    print(f"Available credits: {data['balance']:,.2f}")
    print(f"Frozen credits (in-progress tasks): {data['frozen']:,.2f}")
    return data


def resolve_project(value):
    candidate = Path(value).resolve()
    if candidate.suffix.lower() != '.json':
        raise PipelineError('Use a project JSON file, such as ExampleProjectFiles/UE5Example/project.json.')
    if not candidate.is_file():
        raise PipelineError(f'Project not found: {candidate}')
    return candidate


def project_config(project_file, settings_file, validate=True):
    if not Path(settings_file).is_file():
        raise PipelineError(f'Settings not found: {settings_file}')
    general = config.load_settings(settings_file)
    cfg = config.load(project_file, defaults=general)
    if validate:
        config.validate(cfg)
    return cfg


def publish(run, output, filename='character.glb'):
    source = run / 'final' / 'character.glb'
    metadata = read_json(run / 'final' / 'character.json')
    if sha256(source) != metadata['sha256']:
        raise PipelineError('Final GLB digest differs from its delivery manifest.')
    output.mkdir(parents=True, exist_ok=True)
    # Replace the deliverable only after the completed work artifact is verified.
    temporary = output / (filename + '.tmp')
    shutil.copyfile(source, temporary)
    temporary.replace(output / filename)


def run_project(project_file, settings_file, resume=False, dry_run=False, messy=False,
                job_config=None, output_folder=None, model_name=None):
    project_file = Path(project_file).resolve()
    folder = project_file.parent
    cfg = job_config if job_config is not None else project_config(project_file, settings_file)
    base_work = folder / 'Work'
    if base_work.resolve().parent != folder:
        raise PipelineError('Work must be inside the project folder.')
    work = base_work / model_name if model_name else base_work
    output = Path(output_folder).resolve() if output_folder else folder / 'Output'
    filename = model_name + '.glb' if model_name else 'character.glb'
    if output.is_relative_to(base_work.resolve()):
        raise PipelineError('Output cannot be inside Work, where intermediate files are cleaned up.')
    if dry_run:
        print(json.dumps({'project': str(project_file), 'config': cfg,
                          'work': str(work), 'output': str(output / filename),
                          'keep_work': messy, 'network_requests': 0}, indent=2))
        return output / filename
    lock = folder / '.forge.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PipelineError('Project is locked. Verify the previous process stopped before removing .forge.lock.') from None
    os.write(descriptor, str(os.getpid()).encode())
    os.close(descriptor)
    try:
        current = work / 'current.json'
        if resume:
            if not current.is_file():
                raise PipelineError('No project job to resume. Run without --resume first.')
            run = (work / read_json(current)['run']).resolve()
            if not run.is_relative_to((work / 'runs').resolve()):
                raise PipelineError('Saved job path is outside this project Work/runs folder.')
            if not (run / 'manifest.json').is_file():
                raise PipelineError('Saved job has no manifest. Run without --resume to start a new job.')
        else:
            label = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
            run = work / 'runs' / label
        build = Build(cfg, run, resume=resume)
        build.preflight('blender')
        # Store the job identity before any paid submissions so it can be resumed.
        write_json(current, {'run': run.relative_to(work).as_posix()})
        build.run('blender')
        publish(run, output, filename)
        if not messy:
            # Verify the exact absolute cleanup target before recursive deletion.
            target = run.resolve()
            if not target.is_relative_to(base_work.resolve()) or target.parent != (work / 'runs').resolve() or run.is_symlink():
                raise PipelineError('Refusing cleanup outside the project Work folder.')
            shutil.rmtree(target)
            current.unlink(missing_ok=True)
            # Remove empty containers only; retain earlier failed or --messy jobs.
            for empty in (work / 'runs', work, base_work):
                if empty.is_dir() and not any(empty.iterdir()):
                    empty.rmdir()
        print('Completed: ' + str(output / filename))
        return output / filename
    finally:
        lock.unlink(missing_ok=True)


def plan_batch(project_file, settings_file, input_folder, output_folder, subfolders=False,
               single_image=False, notexture=False, rig=False, topology=None, faces=None,
               pbr=None, delight=None, rig_spec=None, rig_type=None):
    if notexture and pbr is True:
        raise PipelineError('--pbr cannot be combined with --notexture.')
    cfg = project_config(project_file, settings_file, validate=False)
    groups = discover(input_folder, single_image, cfg['name'])
    output = Path(output_folder).resolve()
    if output.is_relative_to((Path(project_file).parent / 'Work').resolve()):
        raise PipelineError('Output cannot be inside Work, where intermediate files are cleaned up.')
    jobs = []
    for group in groups:
        recipe = copy.deepcopy(cfg)
        name = group['name']
        internal_name = re.sub(r'[^A-Za-z0-9_]', '_', name)
        if not internal_name[0].isalpha():
            internal_name = 'Model_' + internal_name
        recipe.update(name=internal_name[:64], source=None, inputs=group['inputs'])
        if topology is not None:
            recipe['tripo']['quad'] = topology == 'quad'
        if faces is not None:
            recipe['tripo']['face_limit'] = faces
        recipe['tripo'].update(texture=not notexture,
                               pbr=(recipe['tripo']['pbr'] if pbr is None else pbr) and not notexture,
                               export_uv=not notexture)
        if delight is not None:
            recipe['tripo']['delight'] = delight
        if notexture:
            recipe['retopology']['bake'] = False
        if rig_spec is not None:
            recipe['rig']['spec'] = rig_spec
            if recipe['rig']['mode'] == 'ue5':
                recipe['rig']['mode'] = 'force'
            if rig_spec in ('mixamo', 'tripo'):
                recipe['output_rig'].update(profile='source', reference=None)
        if rig_type is not None:
            recipe['rig']['type'] = rig_type
        recipe['rig']['mode'] = ('ue5' if recipe['rig']['mode'] == 'ue5' else 'force') if rig else 'none'
        if not rig:
            recipe['output_rig'].update(profile='source', reference=None)
        config.validate(recipe)
        destination = output / name if subfolders else output
        jobs.append({'name': name, 'config': recipe, 'output': destination / (name + '.glb')})
    return jobs


def run_batch(project_file, settings_file, jobs, dry_run=False, messy=False, resume=False):
    print(f'{len(jobs)} model(s) {"would be built" if dry_run else "to build"}:')
    for job in jobs:
        cfg = job['config']
        print(f"  {job['name']} -> {job['output']}")
        print(f"    topology: {'quad' if cfg['tripo']['quad'] else 'triangle'}; face limit: {cfg['tripo']['face_limit']:,}")
        for view, image in cfg['inputs'].items():
            print(f'    {view}: {Path(image).name}')
        print(f"    textures: {'yes' if cfg['tripo']['texture'] else 'no'}; UVs: {'yes' if cfg['tripo']['export_uv'] else 'no'}; rig: {cfg['rig']['mode']}; keep intermediates: {'yes' if messy else 'no'}")
        print(f"    PBR: {'yes' if cfg['tripo']['pbr'] else 'no'}; de-lighting: {'yes' if cfg['tripo']['delight'] else 'no'}; output rig: {cfg['output_rig']['profile']}")
    if dry_run:
        print('Dry run: no API requests, builds or files created.')
        return
    # Verify every recipe's downstream tools before spending any credits.
    for job in jobs:
        Build(job['config'], Path(project_file).parent / 'Work' / job['name'] / 'preflight').preflight('blender')
    for job in jobs:
        run_project(project_file, settings_file, resume=resume, messy=messy,
                    job_config=job['config'], output_folder=job['output'].parent, model_name=job['name'])


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog='TripoForge', description='TripoForge: images to GLB models', add_help=False,
        usage='%(prog)s [<project>.json] [--generate | --balance | --web] [options]',
        epilog='Multiview filenames: robot_front.png, robot_left.png, robot_back.png, robot_right.png. '
               'Each matching group produces robot.glb. Use --singleimage for one model per image.')
    parser.add_argument('-h', '--h', '--help', action='help', help='Show this help and exit')
    parser.add_argument('project', nargs='?', help='Path to a project JSON file')
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument('--generate', action='store_true', help='Generate models (default)')
    commands.add_argument('--balance', action='store_true', help='Display available and frozen credits')
    commands.add_argument('--web', action='store_true', help='Open the local web interface')
    parser.add_argument('--input', metavar='FOLDER', help='Folder containing PNG/JPEG images')
    parser.add_argument('--output', metavar='FOLDER', help='Folder for final GLB files (default: <input>/output)')
    parser.add_argument('--subfolders', action='store_true', help='Put each GLB in a subfolder named for its model')
    parser.add_argument('--settings', default=str(ROOT / 'config.json'), metavar='JSON', help='Shared settings file')
    parser.add_argument('--resume', action='store_true', help='Resume the latest retained job for each model')
    parser.add_argument('--dry-run', action='store_true', help='Preview groups and output paths without building or writing files')
    parser.add_argument('--cost', action='store_true', help='Estimate a new run in credits and USD without building (output folder optional)')
    parser.add_argument('--messy', action='store_true', help='Keep intermediate files and reports in Work after success')
    parser.add_argument('--notexture', action='store_true', help='Omit textures, PBR maps and UV unwrapping')
    parser.add_argument('--pbr', action=argparse.BooleanOptionalAction, default=None, help='Enable PBR maps (--no-pbr disables; default: project setting)')
    parser.add_argument('--delight', action=argparse.BooleanOptionalAction, default=None, help='Enable texture de-lighting (--no-delight disables; default: project setting)')
    parser.add_argument('--rig', action='store_true', help='Add a rigging pass (default: no rigging)')
    parser.add_argument('--topology', choices=('triangle', 'quad'), help='Override the project mesh topology')
    parser.add_argument('--faces', '--face-limit', type=int, metavar='COUNT', help='Override the project polygon limit')
    parser.add_argument('--singleimage', '-singleimage', action='store_true', help='Build one model per image using single-image generation')
    arguments = sys.argv[1:] if argv is None else argv
    if not arguments:
        parser.print_help()
        return 0
    args = parser.parse_args(arguments)
    if args.web:
        if any((args.input, args.output, args.subfolders, args.resume, args.dry_run, args.messy,
                args.notexture, args.rig, args.singleimage, args.cost, args.topology, args.faces is not None,
                args.pbr is not None, args.delight is not None)):
            parser.error('--web accepts a project JSON and --settings; use the browser for generation options')
        from web_server import serve
        try:
            serve(settings=args.settings, project=resolve_project(args.project) if args.project else None)
            return 0
        except (PipelineError, OSError, ValueError) as exc:
            print('ERROR: ' + str(exc), file=sys.stderr)
            return 1
    if args.balance:
        if any((args.input, args.output, args.subfolders, args.resume, args.dry_run, args.messy,
                args.notexture, args.rig, args.singleimage, args.cost, args.topology, args.faces is not None,
                args.pbr is not None, args.delight is not None)):
            parser.error('--balance does not accept generation options')
    elif not args.project or not args.input:
        parser.error('provide a project JSON file and --input FOLDER')
    if args.cost and args.resume:
        parser.error('--cost estimates a full new run; it cannot be combined with --resume')
    try:
        project_file = resolve_project(args.project) if args.project else None
        if args.balance:
            show_balance(args.settings, project_file)
        else:
            output_folder = args.output or Path(args.input) / 'output'
            jobs = plan_batch(project_file, args.settings, args.input, output_folder, args.subfolders,
                              args.singleimage, args.notexture, args.rig, args.topology, args.faces,
                              args.pbr, args.delight)
            if args.cost:
                if args.dry_run:
                    run_batch(project_file, args.settings, jobs, dry_run=True, messy=args.messy)
                show_cost(jobs)
            else:
                run_batch(project_file, args.settings, jobs, args.dry_run, args.messy, args.resume)
        return 0
    except (PipelineError, OSError, ValueError, KeyError) as exc:
        print('ERROR: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
