# Copyright (C) 2026 - Joseph M Wilcox
"""Balance command tests: no paid submissions or real network calls."""
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
from pipeline.common import PipelineError, write_json
from pipeline.tripo import TripoClient


class BalanceTests(unittest.TestCase):
    def test_balance_uses_authenticated_get(self):
        opener = Mock(return_value=io.BytesIO(b'{"code":0,"data":{"balance":1234.5,"frozen":20}}'))
        result = TripoClient('test-key', opener=opener).balance()
        self.assertEqual(result, {'balance': 1234.5, 'frozen': 20})
        request = opener.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(request.full_url, 'https://openapi.tripo3d.ai/v3/account/balance')
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-key')
        self.assertIsNone(request.data)

    def test_invalid_response_is_not_displayed_as_a_balance(self):
        for data in ({'balance': 0}, {'balance': True, 'frozen': 0},
                     {'balance': '10', 'frozen': 0}, {'balance': float('nan'), 'frozen': 0},
                     {'balance': 10, 'frozen': -1}):
            with self.subTest(data=data), patch.object(TripoClient, 'request', return_value=data):
                with self.assertRaises(PipelineError):
                    TripoClient('test-key').balance()

    def test_key_file_overrides_environment_and_supports_bom(self):
        with tempfile.TemporaryDirectory() as folder:
            key = Path(folder) / 'key.txt'
            key.write_text('file-key\n', encoding='utf-8-sig')
            cfg = config.load()
            cfg['key_file'] = str(key)
            with patch.dict('os.environ', {'TRIPO_API_KEY': 'env-key'}):
                self.assertEqual(TripoClient.from_config(cfg).api_key, 'file-key')
                cfg['key_file'] = None
                self.assertEqual(TripoClient.from_config(cfg).api_key, 'env-key')

    def test_cli_balance_uses_settings_without_building_a_project(self):
        with tempfile.TemporaryDirectory() as folder:
            settings = Path(folder) / 'settings.json'
            write_json(settings, {'key_file': None, 'blender': {'executable': 'missing-blender'}})
            stdout = io.StringIO()
            with patch.dict('os.environ', {'TRIPO_API_KEY': 'test-key'}), \
                 patch.object(TripoClient, 'request', return_value={'balance': 1234.5, 'frozen': 20}) as request, \
                 patch.object(forge, 'Build') as build, \
                 patch.object(sys, 'argv', ['forge.py', '--balance', '--settings', str(settings)]), \
                 redirect_stdout(stdout):
                self.assertEqual(forge.main(), 0)
            request.assert_called_once_with('GET', '/account/balance')
            build.assert_not_called()
            self.assertEqual(stdout.getvalue(), 'Available credits: 1,234.50\nFrozen credits (in-progress tasks): 20.00\n')
            self.assertEqual(list(Path(folder).iterdir()), [settings])

    def test_api_failure_returns_nonzero(self):
        with patch.object(sys, 'argv', ['forge.py', '--balance']), \
             patch.object(forge, 'show_balance', side_effect=PipelineError('Account unavailable')), \
             redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(forge.main(), 1)
        self.assertIn('Account unavailable', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
