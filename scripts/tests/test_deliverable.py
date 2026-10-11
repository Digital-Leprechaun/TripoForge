# Copyright (C) 2026 - Joseph M Wilcox
import json
from pathlib import Path
import struct
import tempfile
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from pipeline.common import PipelineError
from pipeline.deliverable import inspect_glb, package
from pipeline.runner import Build
from pipeline.config import load


def fixture(path, document):
    payload = json.dumps(document).encode()
    payload += b' ' * (-len(payload) % 4)
    path.write_bytes(struct.pack('<4sIII4s', b'glTF', 2, len(payload)+20, len(payload), b'JSON')+payload)


class DeliverableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.source = self.folder / 'model.glb'
        self.doc = {'asset': {'version':'2.0'},'meshes':[{'primitives':[{'attributes':{'POSITION':0}}]}]}

    def test_external_textures_rejected(self):
        self.doc['images']=[{'uri':'outside.png'}]
        fixture(self.source,self.doc)
        with self.assertRaises(PipelineError): inspect_glb(self.source)

    def test_profile_requires_skin(self):
        fixture(self.source,self.doc)
        with self.assertRaises(PipelineError):
            package(self.source,self.folder/'final','Character','mixamo',{'success':True},{})

    def test_output_has_no_local_paths(self):
        fixture(self.source,self.doc)
        output,metadata=package(self.source,self.folder/'final','Character','source',{'success':True,'height_m':1.8},{})
        self.assertTrue(output.exists())
        self.assertEqual(metadata['file'],'character.glb')
        self.assertNotIn(str(self.folder),json.dumps(metadata))

    def test_changed_mount_paths_keep_fingerprint(self):
        fixture(self.source,self.doc)
        config=load();config['source']=str(self.source)
        a=Build(config,self.folder/'first')
        relocated=self.folder/'another';relocated.mkdir()
        dest=relocated/'same-content.glb';dest.write_bytes(self.source.read_bytes())
        config['source']=str(dest)
        config['blender']['executable']='/opt/blender/blender'
        config['key_file']='/run/secrets/tripo_key'
        b=Build(config,self.folder/'second')
        self.assertEqual(a.manifest['fingerprint'],b.manifest['fingerprint'])

    def test_rig_profile_changes_fingerprint(self):
        fixture(self.source,self.doc)
        config=load();config['source']=str(self.source)
        a=Build(config,self.folder/'first')
        config['output_rig']['profile']='mixamo'
        b=Build(config,self.folder/'second')
        self.assertNotEqual(a.manifest['fingerprint'],b.manifest['fingerprint'])


if __name__=='__main__':unittest.main()
