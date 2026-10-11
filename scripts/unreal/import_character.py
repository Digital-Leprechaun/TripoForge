# Copyright (C) 2026 - Joseph M Wilcox
"""Synchronous editor-only GLB import. Run with TRIPO_IMPORT_JOB=/absolute/job.json."""
import json
import math
import os
from pathlib import Path
import re
import traceback
import uuid

import unreal


def asset_path(obj):
    return obj.get_path_name() if obj else None


def validate_target_rig(mesh, reference, exact_proportions=False):
    """Compare actual imported UE bind transforms before changing skeleton ownership."""
    source = unreal.SkeletonModifier()
    target = unreal.SkeletonModifier()
    if not source.set_skeletal_mesh(mesh) or not target.set_skeletal_mesh(reference):
        raise RuntimeError('Could not inspect skeletal reference data')
    names = [str(b) for b in source.get_all_bone_names()]
    expected = [str(b) for b in target.get_all_bone_names()]
    if set(names) != set(expected):
        raise RuntimeError('Target skeleton bone mismatch: missing=%s extra=%s' %
                           (sorted(set(expected) - set(names)), sorted(set(names) - set(expected))))
    max_rotation, max_translation, max_scale, max_direction = 0.0, 0.0, 0.0, 0.0
    for bone in expected:
        if source.get_parent_name(bone) != target.get_parent_name(bone):
            raise RuntimeError('Target parent mismatch: ' + bone)
        a, b = source.get_bone_transform(bone, False), target.get_bone_transform(bone, False)
        qa, qb = a.rotation, b.rotation
        dot = abs(qa.x * qb.x + qa.y * qb.y + qa.z * qb.z + qa.w * qb.w)
        rotation = math.degrees(2 * math.acos(min(1.0, dot)))
        translation = (a.translation - b.translation).length()
        scale = max(abs(a.scale3d.x - b.scale3d.x), abs(a.scale3d.y - b.scale3d.y), abs(a.scale3d.z - b.scale3d.z))
        direction = 0.0
        la, lb = a.translation.length(), b.translation.length()
        # IK/attachment helpers track extremities in world space; proportion
        # changes legitimately alter their local translation direction.
        if str(target.get_parent_name(bone)) != 'None' and not bone.startswith(('ik_', 'weapon_')):
            if (la < 1e-4) != (lb < 1e-4):
                raise RuntimeError('Target zero-length bone translation mismatch: ' + bone)
            if la >= 1e-4 and lb >= 1e-4:
                cosine = (a.translation.x*b.translation.x + a.translation.y*b.translation.y + a.translation.z*b.translation.z) / (la*lb)
                direction = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
        if not all(math.isfinite(v) for v in (rotation, translation, scale)):
            raise RuntimeError('Nonfinite reference transform: ' + bone)
        if rotation > 0.1 or scale > 0.001 or direction > 0.1 or (exact_proportions and translation > 0.05):
            raise RuntimeError('Incompatible target bind pose at %s: rotation %.6fdeg translation %.6fcm direction %.6fdeg scale %.6f' %
                               (bone, rotation, translation, direction, scale))
        max_rotation = max(max_rotation, rotation)
        max_translation = max(max_translation, translation)
        max_scale = max(max_scale, scale)
        max_direction = max(max_direction, direction)
    return {'bone_count': len(expected), 'max_rotation_degrees': max_rotation,
            'max_translation_cm': max_translation, 'max_scale_error': max_scale,
            'max_translation_direction_degrees': max_direction,
            'exact_proportions': exact_proportions, 'reference_mesh': asset_path(reference)}


def main(job, report, _validated_target=False):
    source = Path(job['glb'])
    destination = job['destination'].rstrip('/')
    if not source.is_absolute() or not source.is_file() or source.suffix.lower() != '.glb':
        raise ValueError('glb must be an existing absolute .glb path')
    if not re.fullmatch(r'/Game/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+', destination):
        raise ValueError('destination must be a dedicated /Game/... folder with simple asset names')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', job['name']):
        raise ValueError('name must be a valid Unreal asset identifier')
    replace = job.get('replace_existing', False)
    if not isinstance(replace, bool) or not isinstance(job.get('expected_skeletal'), bool):
        raise ValueError('replace_existing and expected_skeletal must be booleans')
    library = unreal.EditorAssetLibrary
    existing = library.list_assets(destination, recursive=True, include_folder=False)
    if existing and not replace:
        raise ValueError('Destination already contains assets; choose a new folder or explicitly replace_existing')
    target = job.get('target') or {}
    references = {}
    for key, expected_class in [('skeleton', unreal.Skeleton), ('reference_mesh', unreal.SkeletalMesh), ('animation_blueprint', unreal.AnimBlueprint), ('retargeter', None)]:
        path = target.get(key)
        if path:
            obj = library.load_asset(path)
            if not obj or (expected_class and not isinstance(obj, expected_class)):
                raise ValueError('Missing or wrong type target reference: ' + key + '=' + path)
            if key == 'retargeter' and obj.get_class().get_name() != 'IKRetargeter':
                raise ValueError('retargeter reference must be an IKRetargeter')
            references[key] = asset_path(obj)
    report['target_references'] = references
    report['retargeting_status'] = 'not_applied'
    if target.get('skeleton') and not target.get('reference_mesh'):
        raise ValueError('Target skeleton assignment requires reference_mesh for exact hierarchy/bind-axis validation')
    if target.get('reference_mesh') and not target.get('skeleton'):
        raise ValueError('reference_mesh requires a target skeleton')
    if target.get('skeleton') and not _validated_target:
        # Import a disposable standalone skeleton first. Only a successful bind
        # comparison may pass the existing skeleton to Interchange; no blind merge.
        stage_job = dict(job)
        stage_job['destination'] = destination + '_RigCheck_' + uuid.uuid4().hex[:12]
        stage_job['target'] = {}
        stage_job['replace_existing'] = False
        stage_report = {'assets': []}
        try:
            main(stage_job, stage_report)
            reference = library.load_asset(target['reference_mesh'])
            skeleton = library.load_asset(target['skeleton'])
            if reference.get_editor_property('skeleton') != skeleton:
                raise RuntimeError('Reference mesh does not use the requested target skeleton')
            checks = []
            for record in stage_report['assets']:
                if record['type'] == 'SkeletalMesh':
                    checks.append(validate_target_rig(library.load_asset(record['path']), reference, target.get('exact_proportions', False)))
            if not checks:
                raise RuntimeError('Target skeleton requires a skeletal mesh')
            report['target_rig_preflight'] = checks
        finally:
            if library.does_directory_exist(stage_job['destination']):
                if not library.delete_directory(stage_job['destination']):
                    raise RuntimeError('Could not remove disposable rig-check assets: ' + stage_job['destination'])
        return main(job, report, _validated_target=True)
    pipeline = unreal.InterchangeGenericAssetsPipeline()
    pipeline.set_editor_property('asset_name', job['name'])
    mesh_pipeline = pipeline.get_editor_property('mesh_pipeline')
    mesh_pipeline.set_editor_property('import_skeletal_meshes', True)
    mesh_pipeline.set_editor_property('import_static_meshes', True)
    if target.get('skeleton'):
        common = pipeline.get_editor_property('common_skeletal_meshes_and_animations_properties')
        common.set_editor_property('skeleton', library.load_asset(target['skeleton']))
        common.set_editor_property('add_curve_metadata_to_skeleton', False)
        mesh_pipeline.set_editor_property('update_skeleton_reference_pose', False)
    params = unreal.ImportAssetParameters()
    params.set_editor_property('is_automated', True)
    params.set_editor_property('replace_existing', replace)
    params.set_editor_property('destination_name', job['name'])
    params.set_editor_property('override_pipelines', [unreal.SoftObjectPath(pipeline.get_path_name())])
    manager = unreal.InterchangeManager.get_interchange_manager_scripted()
    result = manager.import_asset(destination, manager.create_source_data(str(source)), params)
    if result is None or result is False:
        raise RuntimeError('Interchange import failed; inspect Unreal log')
    # UE5.8 returns imported objects; older releases expose only a success boolean.
    if isinstance(result, bool):
        objects = [library.load_asset(p) for p in library.list_assets(destination, recursive=True, include_folder=False)]
    else:
        objects = list(result)
    objects = [obj for obj in objects if obj]
    if not objects:
        raise RuntimeError('Interchange returned no assets')
    records = []
    skeletal_meshes = []
    static_meshes = []
    for obj in objects:
        path = asset_path(obj)
        if not path.startswith(destination + '/'):
            raise RuntimeError('Imported asset outside destination: ' + path)
        record = {'path': path, 'type': obj.get_class().get_name()}
        if isinstance(obj, unreal.SkeletalMesh):
            skeletal_meshes.append(obj)
            skeleton = obj.get_editor_property('skeleton')
            if not skeleton:
                raise RuntimeError('Skeletal mesh has no skeleton: ' + path)
            record['skeleton'] = asset_path(skeleton)
            bounds = obj.get_bounds()
            extent = bounds.box_extent
            origin = bounds.origin
            record['bounds_cm'] = {'center': [origin.x, origin.y, origin.z], 'size': [extent.x * 2, extent.y * 2, extent.z * 2]}
            record['materials'] = [asset_path(m.get_editor_property('material_interface')) for m in obj.get_editor_property('materials')]
        elif isinstance(obj, unreal.StaticMesh):
            static_meshes.append(obj)
            record['materials'] = [asset_path(m.get_editor_property('material_interface')) for m in obj.get_editor_property('static_materials')]
        records.append(record)
    report['assets'] = records
    if job['expected_skeletal'] and not skeletal_meshes:
        raise RuntimeError('Expected skeletal mesh, but no SkeletalMesh was imported')
    expected_height = job.get('expected_height_cm')
    if expected_height is not None:
        expected_height = float(expected_height)
        if expected_height <= 0:
            raise ValueError('expected_height_cm must be positive')
        mesh_records = [r for r in records if r.get('bounds_cm')]
        if not mesh_records:
            raise RuntimeError('Height validation requires a skeletal mesh')
        bottom = min(r['bounds_cm']['center'][2] - r['bounds_cm']['size'][2] / 2 for r in mesh_records)
        top = max(r['bounds_cm']['center'][2] + r['bounds_cm']['size'][2] / 2 for r in mesh_records)
        actual_height = top - bottom
        report['height_cm'] = actual_height
        if abs(actual_height - expected_height) > max(0.5, expected_height * 0.01):
            raise RuntimeError('Imported height %.3f cm differs from expected %.3f cm' % (actual_height, expected_height))
    if not skeletal_meshes and not static_meshes:
        raise RuntimeError('Import produced no meshes')
    if target.get('skeleton'):
        reference = library.load_asset(target['reference_mesh'])
        target_skeleton = library.load_asset(target['skeleton'])
        if reference.get_editor_property('skeleton') != target_skeleton:
            raise RuntimeError('Reference mesh does not use the requested target skeleton')
        blueprint = library.load_asset(target['animation_blueprint']) if target.get('animation_blueprint') else None
        if blueprint and blueprint.get_editor_property('target_skeleton') != target_skeleton:
            raise RuntimeError('Animation Blueprint does not use the requested target skeleton')
        # Validate every mesh before assigning any. Never merge bones into the target.
        checks = [validate_target_rig(mesh, reference, target.get('exact_proportions', False)) for mesh in skeletal_meshes]
        for mesh in skeletal_meshes:
            if mesh.get_editor_property('skeleton') != target_skeleton:
                raise RuntimeError('Interchange did not assign the validated target skeleton')
        for record in records:
            if record['type'] == 'SkeletalMesh':
                record['skeleton'] = asset_path(target_skeleton)
        report['target_rig_validation'] = checks
        report['retargeting_status'] = 'direct_shared_skeleton_binding'
    for obj in objects:
        if not library.save_loaded_asset(obj, only_if_is_dirty=False):
            raise RuntimeError('Could not save ' + asset_path(obj))
    report['skeletal_mesh_count'] = len(skeletal_meshes)
    report['static_mesh_count'] = len(static_meshes)
    report['status'] = 'success'


if __name__ == '__main__':
    job_path = Path(os.environ['TRIPO_IMPORT_JOB'])
    if not job_path.is_absolute():
        raise ValueError('TRIPO_IMPORT_JOB must be an absolute JSON path')
    job = json.loads(job_path.read_text(encoding='utf-8-sig'))
    report_path = Path(job['report'])
    if not report_path.is_absolute():
        raise ValueError('report must be an absolute path')
    report = {'status': 'failed', 'engine_version': unreal.SystemLibrary.get_engine_version(), 'glb': job.get('glb'), 'destination': job.get('destination'), 'assets': []}
    try:
        main(job, report)
    except Exception as exc:
        report['error'] = str(exc)
        unreal.log_error(traceback.format_exc())
        raise
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = report_path.with_suffix(report_path.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(report_path)
