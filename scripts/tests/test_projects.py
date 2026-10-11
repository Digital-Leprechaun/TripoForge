# Copyright (C) 2026 - Joseph M Wilcox
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import forge
from pipeline.common import PipelineError, sha256, write_json


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'ExampleProjectFiles' / 'MixamoExample'
        (self.project / 'Source').mkdir(parents=True)
        for view in ('front', 'left', 'back', 'right'):
            (self.project / 'Source' / (view + '.png')).write_bytes(b'fixture')
        self.settings = self.root / 'config.json'
        write_json(self.settings, {'key_file': 'private/key.txt', 'blender': {'height_m': 2.0}})
        self.file = self.project / 'project.json'
        write_json(self.file, {'name': 'MixamoExample', 'inputs': {
            v: 'Source/' + v + '.png' for v in ('front', 'left', 'back', 'right')},
            'blender': {'height_m': 1.0}, 'rig': {'mode': 'none'}})

    def test_settings_and_project_paths_have_separate_bases(self):
        cfg = forge.project_config(self.file, self.settings)
        self.assertEqual(cfg['key_file'], str(self.root / 'private' / 'key.txt'))
        self.assertEqual(cfg['inputs']['front'], str(self.project / 'Source' / 'front.png'))
        self.assertEqual(cfg['blender']['height_m'], 1.0)

    def test_local_settings_override_shared_and_project_overrides_local(self):
        write_json(self.root / 'config.local.json', {'_copyright': 'Copyright (C) 2026 - Joseph M Wilcox',
            'key_file': 'local/key.txt', 'blender': {'height_m': 3.0}})
        cfg = forge.project_config(self.file, self.settings)
        self.assertEqual(cfg['key_file'], str(self.root / 'local' / 'key.txt'))
        self.assertEqual(cfg['blender']['height_m'], 1.0)
        self.assertNotIn('_copyright', cfg)

    def test_explicit_local_settings_does_not_load_second_overlay(self):
        selected = self.root / 'custom.local.json'
        write_json(selected, {'key_file': 'chosen.txt'})
        write_json(self.root / 'custom.local.local.json', {'key_file': 'wrong.txt'})
        cfg = forge.project_config(self.file, selected)
        self.assertEqual(cfg['key_file'], str(self.root / 'chosen.txt'))

    def test_dry_run_never_creates_work_or_calls_build(self):
        with patch.object(forge, 'Build') as build, redirect_stdout(io.StringIO()):
            forge.run_project(self.file, self.settings, dry_run=True)
            build.assert_not_called()
        self.assertFalse((self.project / 'Work').exists())

    def test_resume_cannot_escape_project(self):
        write_json(self.project / 'Work' / 'current.json', {'run': '../../elsewhere'})
        with self.assertRaisesRegex(PipelineError, 'outside'):
            forge.run_project(self.file, self.settings, resume=True)
        self.assertFalse((self.project / '.forge.lock').exists())

    def test_lock_does_not_get_stolen(self):
        lock = self.project / '.forge.lock'
        lock.write_text('another process')
        with self.assertRaisesRegex(PipelineError, 'locked'):
            forge.run_project(self.file, self.settings)
        self.assertTrue(lock.exists())

    def test_publish_rejects_tampering_before_replacing_output(self):
        run = self.project / 'Work' / 'runs' / 'test'
        (run / 'final').mkdir(parents=True)
        (run / 'final' / 'character.glb').write_bytes(b'changed')
        write_json(run / 'final' / 'character.json', {'sha256': 'incorrect'})
        output = self.project / 'Output'
        output.mkdir()
        old = output / 'character.glb'
        old.write_bytes(b'previous')
        with self.assertRaises(PipelineError):
            forge.publish(run, output)
        self.assertEqual(old.read_bytes(), b'previous')

    def fake_build(self, fail=False):
        class FakeBuild:
            def __init__(self, cfg, directory, resume=False):
                self.directory = Path(directory)

            def preflight(self, until):
                pass

            def run(self, until):
                write_json(self.directory / 'manifest.json', {'test': True})
                if fail:
                    raise PipelineError('Generation failed')
                source = self.directory / 'final' / 'character.glb'
                source.parent.mkdir()
                source.write_bytes(b'completed test artifact')
                write_json(source.with_suffix('.json'), {'sha256': sha256(source)})
        return FakeBuild

    def test_default_success_publishes_only_glb_and_cleans_work(self):
        with patch.object(forge, 'Build', self.fake_build()):
            result = forge.run_project(self.file, self.settings)
        self.assertTrue(result.is_file())
        self.assertEqual([p.name for p in result.parent.iterdir()], ['character.glb'])
        self.assertFalse((self.project / 'Work').exists())

    def test_messy_success_keeps_job_manifest(self):
        with patch.object(forge, 'Build', self.fake_build()):
            forge.run_project(self.file, self.settings, messy=True)
        self.assertTrue((self.project / 'Work' / 'current.json').is_file())
        self.assertEqual(len(list((self.project / 'Work').rglob('manifest.json'))), 1)

    def test_failure_keeps_work_and_previous_output(self):
        output = self.project / 'Output' / 'character.glb'
        output.parent.mkdir()
        output.write_bytes(b'previous')
        with patch.object(forge, 'Build', self.fake_build(fail=True)):
            with self.assertRaisesRegex(PipelineError, 'Generation failed'):
                forge.run_project(self.file, self.settings)
        self.assertTrue((self.project / 'Work' / 'current.json').is_file())
        self.assertEqual(output.read_bytes(), b'previous')


if __name__ == '__main__':
    unittest.main()
