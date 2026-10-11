# Copyright (C) 2026 - Joseph M Wilcox
"""Offline grouping, CLI and bulk publication checks."""
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forge
from pipeline import config
from pipeline.common import PipelineError, sha256, write_json
from pipeline.discovery import discover
from pipeline.runner import Build


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.images = self.root / 'images'
        self.images.mkdir()
        self.project = self.root / 'project.json'
        self.settings = self.root / 'settings.json'
        write_json(self.project, {'name': 'Example'})
        write_json(self.settings, {'key_file': None})
        self.output = self.root / 'output'

    def image(self, name):
        p = self.images / name
        p.write_bytes(b'fixture')
        return p

    def group(self, name):
        for view in ('front', 'left', 'back', 'right'):
            self.image(f'{name}_{view}.png')

    def plan(self, **options):
        return forge.plan_batch(self.project, self.settings, self.images, self.output, **options)

    def test_twelve_images_make_three_models(self):
        for name in ('robot', 'crate', 'alien'):
            self.group(name)
        jobs = self.plan()
        self.assertEqual([j['name'] for j in jobs], ['alien', 'crate', 'robot'])
        self.assertEqual([j['output'].name for j in jobs], ['alien.glb', 'crate.glb', 'robot.glb'])
        self.assertTrue(all(len(j['config']['inputs']) == 4 for j in jobs))

    def test_labels_are_case_insensitive_and_support_prefixes_and_separators(self):
        for name in ('FRONT-Robot.JPG', 'robot.left.png', 'robot_BACK.jpeg', 'right Robot.png'):
            self.image(name)
        result = discover(self.images)
        self.assertEqual(len(result), 1)
        self.assertEqual(set(result[0]['inputs']), {'front', 'left', 'back', 'right'})

    def test_bare_views_use_project_name(self):
        for view in ('front', 'left', 'back', 'right'):
            self.image(view + '.png')
        self.assertEqual(self.plan()[0]['name'], 'Example')

    def test_missing_duplicate_and_unlabelled_images_fail_before_build(self):
        self.image('robot_front.png')
        with self.assertRaisesRegex(PipelineError, 'missing views'):
            self.plan()
        self.image('ROBOT_FRONT.jpg')
        with self.assertRaisesRegex(PipelineError, 'Duplicate front'):
            self.plan()
        self.image('unknown.png')
        with self.assertRaises(PipelineError):
            self.plan()
        self.assertFalse(self.output.exists())

    def test_singleimage_keeps_each_image_separate_and_subfolders_are_named(self):
        self.image('robot_front.png')
        self.image('crate.png')
        jobs = self.plan(single_image=True, subfolders=True, notexture=True, rig=True)
        self.assertEqual(len(jobs), 2)
        for job in jobs:
            self.assertEqual(job['output'], self.output / job['name'] / (job['name'] + '.glb'))
            self.assertEqual(set(job['config']['inputs']), {'image'})
            self.assertFalse(job['config']['tripo']['texture'])
            self.assertFalse(job['config']['tripo']['pbr'])
            self.assertFalse(job['config']['tripo']['export_uv'])
            self.assertEqual(job['config']['rig']['mode'], 'force')

    def test_singleimage_same_stem_cannot_overwrite(self):
        self.image('robot.png')
        self.image('robot.jpg')
        with self.assertRaisesRegex(PipelineError, 'Duplicate image'):
            self.plan(single_image=True)

    def test_ue5_spec_uses_shared_converter_and_requires_rig_flag(self):
        self.group('robot')
        reference = self.root / 'Converter' / 'reference.blend'
        reference.parent.mkdir()
        reference.write_bytes(b'reference fixture')
        write_json(self.settings, {'mixamo2ue5': {'reference': 'Converter/reference.blend'}})
        write_json(self.project, {'rig': {'mode': 'force', 'spec': 'ue5'}})
        cfg = self.plan(rig=True)[0]['config']
        self.assertEqual(cfg['rig'], {'mode': 'force', 'type': 'biped', 'spec': 'mixamo'})
        self.assertEqual(cfg['output_rig'], {'profile': 'ue5', 'reference': str(reference), 'weights': 'reference'})
        reference.unlink()
        skipped = self.plan()[0]['config']
        self.assertEqual(skipped['rig']['mode'], 'none')
        self.assertEqual(skipped['output_rig']['profile'], 'source')
        with self.assertRaisesRegex(PipelineError, 'reference'):
            self.plan(rig=True)

    def test_ue5_recipe_is_portable_to_an_external_project_folder(self):
        self.group('robot')
        write_json(self.project, {'rig': {'spec': 'ue5'}})
        self.assertEqual(Path(config.DEFAULTS['mixamo2ue5']['reference']), forge.ROOT / 'Converters' / 'Mixamo2UE5' / 'reference.blend')
        reference = self.root / 'installation' / 'reference.blend'
        reference.parent.mkdir()
        reference.write_bytes(b'private reference fixture')
        # Simulate an installed local reference without relying on Epic assets.
        with patch.dict(config.DEFAULTS['mixamo2ue5'], {'reference': str(reference)}):
            cfg = self.plan(rig=True)[0]['config']
        self.assertEqual(Path(cfg['output_rig']['reference']), reference)
        self.assertEqual(cfg['output_rig']['profile'], 'ue5')

    def test_material_cli_overrides_both_directions_and_preserves_json_when_omitted(self):
        self.group('robot')
        write_json(self.project, {'tripo': {'pbr': False, 'delight': False}})
        self.assertFalse(self.plan()[0]['config']['tripo']['pbr'])
        self.assertFalse(self.plan()[0]['config']['tripo']['delight'])
        for flags, expected in ((['--pbr', '--delight'], True), (['--no-pbr', '--no-delight'], False)):
            with patch.object(forge, 'run_batch') as run:
                self.assertEqual(forge.main([str(self.project), '--input', str(self.images),
                                             '--settings', str(self.settings), *flags]), 0)
                cfg = run.call_args.args[2][0]['config']
                self.assertEqual(cfg['tripo']['pbr'], expected)
                self.assertEqual(cfg['tripo']['delight'], expected)
        with self.assertRaisesRegex(PipelineError, 'cannot be combined'):
            self.plan(pbr=True, notexture=True)
        self.assertFalse(self.plan(notexture=True)[0]['config']['tripo']['pbr'])

    def test_ue5_spec_rejects_non_humanoid_conversion_before_build(self):
        self.group('robot')
        write_json(self.project, {'rig': {'spec': 'ue5', 'type': 'quadruped'}})
        with self.assertRaisesRegex(PipelineError, 'biped'):
            self.plan(rig=True)

    def test_ue5_spec_preserves_internal_policy_and_legacy_alias_still_loads(self):
        for policy in ('none', 'auto', 'force'):
            cfg = config.load()
            cfg['rig'].update(mode=policy, spec='ue5')
            config.normalize_rig(cfg)
            self.assertEqual(cfg['rig']['mode'], policy)
            self.assertEqual(cfg['rig']['spec'], 'mixamo')
            self.assertEqual(cfg['output_rig']['profile'], 'source' if policy == 'none' else 'ue5')
        self.group('robot')
        reference = self.root / 'reference.blend'
        reference.write_bytes(b'private reference fixture')
        write_json(self.settings, {'mixamo2ue5': {'reference': str(reference)}})
        write_json(self.project, {'rig': {'mode': 'ue5'}})
        legacy = self.plan(rig=True)[0]['config']
        self.assertEqual(legacy['rig']['spec'], 'mixamo')
        self.assertEqual(legacy['output_rig']['profile'], 'ue5')

    def test_topology_and_face_limit_cli_override_json_and_update_cost(self):
        self.group('robot')
        write_json(self.project, {'tripo': {'model': 'P2-20260801', 'face_limit': 20000, 'quad': False}})
        from pipeline.pricing import estimate
        base = estimate(self.plan()[0]['config'])['max']
        for topology, faces, surcharge in (('quad', 5000, 5), ('triangle', 10000, 0)):
            with self.subTest(topology=topology), patch.object(forge, 'run_batch') as run:
                self.assertEqual(forge.main([str(self.project), '--generate', '--input', str(self.images),
                                             '--settings', str(self.settings), '--topology', topology,
                                             '--faces', str(faces)]), 0)
                cfg = run.call_args.args[2][0]['config']
                self.assertEqual(cfg['tripo']['quad'], topology == 'quad')
                self.assertEqual(cfg['tripo']['face_limit'], faces)
                self.assertEqual(estimate(cfg)['max'], base + surcharge)
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(forge.main([str(self.project), '--input', str(self.images),
                                         '--settings', str(self.settings), '--topology', 'quad',
                                         '--face-limit', '5000', '--cost']), 0)
        self.assertIn('topology: quad; face limit: 5,000', output.getvalue())
        self.assertIn('Total: 125 credits', output.getvalue())

    def test_invalid_face_limits_and_p1_quads_fail_before_build(self):
        self.group('robot')
        for model, topology, faces in (('P2-20260801', 'quad', 25001),
                                       ('P2-20260801', 'triangle', 50001),
                                       ('P2-20260801', 'triangle', 0),
                                       ('P1-20260311', 'quad', 5000)):
            write_json(self.project, {'tripo': {'model': model, 'face_limit': 10000}})
            with self.subTest(model=model, topology=topology, faces=faces), \
                 patch.object(forge, 'Build') as build, redirect_stderr(io.StringIO()):
                result = forge.main([str(self.project), '--input', str(self.images),
                                     '--settings', str(self.settings), '--topology', topology,
                                     '--faces', str(faces)])
                self.assertEqual(result, 1)
                build.assert_not_called()

    def test_unspecified_topology_and_faces_preserve_json_defaults(self):
        self.group('robot')
        write_json(self.project, {'tripo': {'model': 'P2-20260801', 'face_limit': 4321, 'quad': True}})
        cfg = self.plan()[0]['config']
        self.assertTrue(cfg['tripo']['quad'])
        self.assertEqual(cfg['tripo']['face_limit'], 4321)

    def test_dry_run_previews_outputs_and_never_builds_or_creates_files(self):
        self.group('robot')
        before = set(self.root.rglob('*'))
        with patch.object(forge, 'Build') as build, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(forge.main([str(self.project), '--generate', '--input', str(self.images),
                                         '--output', str(self.output), '--dry-run']), 0)
        build.assert_not_called()
        self.assertEqual(before, set(self.root.rglob('*')))
        self.assertIn(str(self.output / 'robot.glb'), output.getvalue())

    def test_help_aliases_and_empty_invocation_succeed_without_build(self):
        for args in ([], ['-h'], ['--h'], ['--help']):
            with self.subTest(args=args), redirect_stdout(io.StringIO()) as output:
                if args:
                    with self.assertRaises(SystemExit) as result:
                        forge.main(args)
                    self.assertEqual(result.exception.code, 0)
                else:
                    self.assertEqual(forge.main(args), 0)
                self.assertIn('--generate', output.getvalue())
                self.assertIn('--balance', output.getvalue())

    def test_omitted_output_defaults_to_input_output(self):
        self.group('robot')
        for subfolders in (False, True):
            with self.subTest(subfolders=subfolders), patch.object(forge, 'run_batch') as run:
                args = [str(self.project), '--generate', '--input', str(self.images),
                        '--settings', str(self.settings)]
                if subfolders:
                    args.append('--subfolders')
                self.assertEqual(forge.main(args), 0)
                job = run.call_args.args[2][0]
                expected = self.images / 'output'
                if subfolders:
                    expected /= 'robot'
                self.assertEqual(job['output'], expected / 'robot.glb')
        self.assertFalse((self.images / 'output').exists())

    def test_dry_run_with_default_output_does_not_create_folder(self):
        self.group('robot')
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(forge.main([str(self.project), '--input', str(self.images),
                                         '--settings', str(self.settings), '--dry-run']), 0)
        self.assertIn(str(self.images / 'output' / 'robot.glb'), output.getvalue())
        self.assertFalse((self.images / 'output').exists())

    def test_mutually_exclusive_commands(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
            forge.main(['--generate', '--balance'])
        self.assertEqual(result.exception.code, 2)

    def test_batch_publication_and_cleanup_preserve_other_retained_runs(self):
        self.group('robot')
        self.group('crate')
        old = self.root / 'Work' / 'robot' / 'runs' / 'old-failed' / 'manifest.json'
        write_json(old, {'status': 'failed', 'task_id': 'paid-task'})
        class FakeBuild:
            def __init__(self, cfg, directory, resume=False):
                self.directory = Path(directory)
            def preflight(self, until):
                pass
            def run(self, until):
                source = self.directory / 'final' / 'character.glb'
                source.parent.mkdir(parents=True)
                source.write_bytes(b'completed fixture')
                write_json(source.with_suffix('.json'), {'sha256': sha256(source)})
        with patch.object(forge, 'Build', FakeBuild), redirect_stdout(io.StringIO()):
            forge.run_batch(self.project, self.settings, self.plan(subfolders=True))
        self.assertTrue(old.is_file())
        for name in ('crate', 'robot'):
            self.assertTrue((self.output / name / (name + '.glb')).is_file())
        self.assertFalse((self.root / 'Work' / 'crate').exists())

    def test_single_image_runner_uses_single_image_endpoint(self):
        image = self.image('crate.png')
        cfg = config.load()
        cfg['inputs'] = {'image': str(image)}
        config.validate(cfg)
        build = Build(cfg, self.root / 'build', client=Mock())
        with patch.object(build, 'upload', return_value='file-token') as upload, \
             patch.object(build, 'remote', return_value=image) as remote:
            build.run('tripo')
        upload.assert_called_once_with('image', str(image))
        self.assertEqual(remote.call_args.args[1], '/generation/image-to-model')
        self.assertEqual(remote.call_args.args[2]['input'], 'file-token')
        self.assertNotIn('inputs', remote.call_args.args[2])


if __name__ == '__main__':
    unittest.main()
