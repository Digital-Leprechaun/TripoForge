# Copyright (C) 2026 - Joseph M Wilcox
"""Published-rate calculations and safe CLI estimate behavior."""
import copy
import io
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forge
from pipeline import config
from pipeline.common import write_json
from pipeline.pricing import estimate, show_cost


class PricingTests(unittest.TestCase):
    def recipe(self, model='P2-20260801'):
        cfg = copy.deepcopy(config.DEFAULTS)
        cfg['tripo']['model'] = model
        cfg['inputs'] = {'image': 'fixture.png'}
        return cfg

    def test_model_specific_bases_and_geometry_surcharge(self):
        for model, expected in [('v3.1-20260211', 60), ('v3.0-20250812', 60),
                                ('P1-20260311', 60), ('P2-20260801', 120)]:
            with self.subTest(model=model):
                cfg = self.recipe(model)
                self.assertEqual(estimate(cfg)['max'], expected)
                cfg['inputs'] = {v: v + '.png' for v in ('front', 'left', 'back', 'right')}
                self.assertEqual(estimate(cfg)['max'], expected)

    def test_texture_tiers_and_no_texture(self):
        cfg = self.recipe()
        for tier, expected in [('fast', 110), ('standard', 110), ('detailed', 120), ('extreme', 130)]:
            with self.subTest(tier=tier):
                cfg['tripo']['texture_quality'] = tier
                self.assertEqual(estimate(cfg)['max'], expected)
        cfg['tripo'].update(texture=False, pbr=False)
        self.assertEqual(estimate(cfg)['max'], 100)

    def test_quad_retopology_and_rig_are_added_once(self):
        cfg = self.recipe()
        cfg['tripo']['quad'] = True
        cfg['retopology']['enabled'] = True
        cfg['rig']['mode'] = 'force'
        self.assertEqual(estimate(cfg)['max'], 180)
        cfg['retopology']['model'] = 'v1.0'
        self.assertEqual(estimate(cfg)['max'], 160)

    def test_auto_rig_range_and_local_source(self):
        cfg = self.recipe()
        cfg['rig']['mode'] = 'auto'
        self.assertEqual((estimate(cfg)['min'], estimate(cfg)['max']), (120, 145))
        cfg['source'] = 'local.glb'
        self.assertEqual((estimate(cfg)['min'], estimate(cfg)['max']), (0, 25))

    def test_three_models_total_and_usd(self):
        jobs = [{'name': n, 'config': self.recipe()} for n in ('robot', 'alien', 'crate')]
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(show_cost(jobs), {'min': 360, 'max': 360})
        self.assertIn('Total: 360 credits ($3.60 USD)', output.getvalue())

    def test_cost_cli_needs_no_output_tools_or_key_and_never_builds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = root / 'Images'
            inputs.mkdir()
            (inputs / 'crate.png').write_bytes(b'fixture')
            project, settings = root / 'project.json', root / 'config.json'
            write_json(project, {'tripo': {'model': 'P2-20260801', 'face_limit': 20000}})
            write_json(settings, {'key_file': 'missing-key.txt', 'blender': {'executable': 'missing-blender'}})
            before = set(root.rglob('*'))
            with patch.object(forge, 'Build') as build, patch.object(forge, 'TripoClient') as client, \
                 redirect_stdout(io.StringIO()) as output:
                result = forge.main([str(project), '--generate', '--cost', '--input', str(inputs),
                                     '--settings', str(settings), '--singleimage', '--notexture', '--rig'])
            self.assertEqual(result, 0)
            self.assertIn('Total: 125 credits ($1.25 USD)', output.getvalue())
            build.assert_not_called()
            client.assert_not_called()
            self.assertEqual(before, set(root.rglob('*')))

    def test_balance_and_resume_cannot_use_cost(self):
        for args in (['--balance', '--cost'], ['project.json', '--input', 'Images', '--cost', '--resume']):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                forge.main(args)
            self.assertEqual(result.exception.code, 2)

    def test_flags_override_project_enable_switches_for_all_cost_combinations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / 'Images'
            images.mkdir()
            (images / 'robot.png').write_bytes(b'fixture')
            project, settings = root / 'project.json', root / 'settings.json'
            write_json(settings, {})
            write_json(project, {
                'tripo': {'model': 'P2-20260801', 'face_limit': 20000,
                          'texture': False, 'pbr': False, 'export_uv': False},
                'rig': {'mode': 'force'}, 'output_rig': {'profile': 'mixamo'}})
            for rig, notexture, expected in ((False, False, 120), (True, False, 145),
                                            (False, True, 100), (True, True, 125)):
                with self.subTest(rig=rig, notexture=notexture):
                    jobs = forge.plan_batch(project, settings, images, root / 'Output',
                                            single_image=True, rig=rig, notexture=notexture)
                    cfg = jobs[0]['config']
                    self.assertEqual(cfg['rig']['mode'], 'force' if rig else 'none')
                    self.assertEqual(cfg['output_rig']['profile'], 'mixamo' if rig else 'source')
                    for flag in ('texture', 'export_uv'):
                        self.assertEqual(cfg['tripo'][flag], not notexture)
                    self.assertFalse(cfg['tripo']['pbr'])  # Explicit JSON material settings are respected.
                    self.assertEqual(estimate(cfg)['max'], expected)
                    flags = (['--rig'] if rig else []) + (['--notexture'] if notexture else [])
                    with redirect_stdout(io.StringIO()) as output, patch.object(forge, 'Build') as build:
                        result = forge.main([str(project), '--cost', '--input', str(images),
                                             '--settings', str(settings), '--singleimage', *flags])
                    self.assertEqual(result, 0)
                    self.assertIn(f'Total: {expected} credits', output.getvalue())
                    self.assertIn('UV unwrapping: omitted' if notexture else 'UV unwrapping: included', output.getvalue())
                    build.assert_not_called()


if __name__ == '__main__':
    unittest.main()
