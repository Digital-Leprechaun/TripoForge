# Copyright (C) 2026 - Joseph M Wilcox
"""Editor-hosted exact bind + real animation CPU skin test; isolated test project only.

TRIPO_RIG_TEST_JOB points at an import job JSON. This imports into that project's
temporary destination and needs the compiled scripts/tests/unreal/RigValidationLibrary.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path

import unreal

job = json.loads(Path(os.environ['TRIPO_RIG_TEST_JOB']).read_text(encoding='utf-8-sig'))
report_path = Path(job['report'])
report = {'status': 'failed', 'assets': [], 'glb': job['glb'],
          'sha256': hashlib.sha256(Path(job['glb']).read_bytes()).hexdigest()}
try:
    importer_path = Path(__file__).resolve().parents[1] / 'unreal' / 'import_character.py'
    spec = importlib.util.spec_from_file_location('tripo_importer', importer_path)
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    importer.main(job, report)
    animation = unreal.EditorAssetLibrary.load_asset(job['test_animation'])
    assert isinstance(animation, unreal.AnimSequence)
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    results = []
    for record in report['assets']:
        if record['type'] != 'SkeletalMesh':
            continue
        mesh = unreal.EditorAssetLibrary.load_asset(record['path'])
        result = json.loads(unreal.RigValidationLibrary.validate_animation(world, mesh, animation))
        results.append(result)
        assert result['success'], result
        options = unreal.AnimPoseEvaluationOptions()
        options.set_editor_property('optional_skeletal_mesh', mesh)
        pose = unreal.AnimPoseExtensions.get_anim_pose_at_time(animation, animation.get_play_length() * .37, options)
        modifier = unreal.SkeletonModifier()
        assert modifier.set_skeletal_mesh(mesh)
        sampled = {}
        for bone in modifier.get_all_bone_names():
            t = unreal.AnimPoseExtensions.get_bone_pose(pose, bone, unreal.AnimPoseSpaces.WORLD)
            sampled[str(bone)] = {'translation': [t.translation.x, t.translation.y, t.translation.z],
                                 'rotation_xyzw': [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w],
                                 'scale': [t.scale3d.x, t.scale3d.y, t.scale3d.z]}
        report['sampled_pose_world_cm'] = sampled
    assert results
    report['animation_deformation'] = results
    report['test_animation'] = animation.get_path_name()
    report['status'] = 'success'
except Exception as exc:
    report['status'] = 'failed'
    report['error'] = str(exc)
    diagnostics = {}
    for record in report.get('assets', []):
        if record['type'] != 'SkeletalMesh':
            continue
        mesh = unreal.EditorAssetLibrary.load_asset(record['path'])
        modifier = unreal.SkeletonModifier()
        if mesh and modifier.set_skeletal_mesh(mesh):
            bones = {}
            for bone in modifier.get_all_bone_names():
                t = modifier.get_bone_transform(bone, False)
                bones[str(bone)] = {'parent': str(modifier.get_parent_name(bone)),
                                    'translation': [t.translation.x, t.translation.y, t.translation.z],
                                    'rotation_xyzw': [t.rotation.x, t.rotation.y, t.rotation.z, t.rotation.w],
                                    'scale': [t.scale3d.x, t.scale3d.y, t.scale3d.z]}
            diagnostics[record['path']] = bones
    report['imported_bind_diagnostics'] = diagnostics
    raise
finally:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
