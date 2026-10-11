# Copyright (C) 2026 - Joseph M Wilcox
"""Export weighted reference geometry and raw bone transforms from Unreal Editor.

Set TRIPOFORGE_REFERENCE_JOB to JSON with mesh asset path and output directory.
"""
import json
import os
from pathlib import Path
import unreal

job = json.loads(Path(os.environ['TRIPOFORGE_REFERENCE_JOB']).read_text(encoding='utf-8-sig'))
out = Path(job['directory'])
out.mkdir(parents=True, exist_ok=True)
mesh = unreal.EditorAssetLibrary.load_asset(job['mesh'])
if not isinstance(mesh, unreal.SkeletalMesh):
    raise RuntimeError('Reference is not a skeletal mesh: ' + job['mesh'])
modifier = unreal.SkeletonModifier()
if not modifier.set_skeletal_mesh(mesh):
    raise RuntimeError('Cannot read reference skeleton')

def transform(t):
    return {'translation': [t.translation.x, t.translation.y, t.translation.z],
            'rotation_xyzw': [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w],
            'scale': [t.scale3d.x, t.scale3d.y, t.scale3d.z]}

bones = [{'name': str(b), 'parent': str(modifier.get_parent_name(b)),
          'local': transform(modifier.get_bone_transform(b, False)),
          'world': transform(modifier.get_bone_transform(b, True))}
         for b in modifier.get_all_bone_names()]
options = unreal.FbxExportOption()
options.set_editor_property('bake_material_inputs', unreal.FbxMaterialBakeMode.DISABLED)
task = unreal.AssetExportTask()
task.set_editor_properties({'object': mesh, 'filename': str(out / 'reference.fbx'),
                           'automated': True, 'prompt': False, 'replace_identical': True, 'options': options})
if not unreal.Exporter.run_asset_export_task(task):
    raise RuntimeError('FBX reference export failed')
report = {'mesh': mesh.get_path_name(), 'skeleton': mesh.skeleton.get_path_name(),
          'bones': bones, 'height_cm': mesh.get_bounds().box_extent.z * 2, 'fbx_exported': True}
(out / 'reference.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print('REFERENCE_EXPORT_SUCCESS bones=' + str(len(bones)))
