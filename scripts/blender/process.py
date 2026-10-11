# Copyright (C) 2026 - Joseph M Wilcox
"""Headless GLB/FBX bind-pose normalization. Run with Blender Python."""
import argparse
import json
import math
import sys
from pathlib import Path

import bpy
from mathutils import Matrix, Vector


def bounds(meshes):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = [obj.evaluated_get(depsgraph).matrix_world @ Vector(corner)
              for obj in meshes for corner in obj.evaluated_get(depsgraph).bound_box]
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])


def process(args, report):
    source, target = Path(args.input).resolve(), Path(args.output).resolve()
    if not source.is_file() or source.suffix.lower() not in {'.glb', '.fbx'}:
        raise ValueError('Input must be an existing GLB or FBX file')
    if target.suffix.lower() != '.glb' or target == source:
        raise ValueError('Output must be a distinct .glb path')
    if not math.isfinite(args.height_m) or args.height_m <= 0:
        raise ValueError('height-m must be finite and positive')
    if not math.isfinite(args.yaw_degrees):
        raise ValueError('yaw-degrees must be finite')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if source.suffix.lower() == '.glb':
        bpy.ops.import_scene.gltf(filepath=str(source))
    else:
        bpy.ops.import_scene.fbx(filepath=str(source), use_anim=True)
    # The glTF importer creates helper meshes for custom bone displays; these are
    # viewport widgets, not asset geometry and must not affect height or validation.
    widgets = {bone.custom_shape for obj in bpy.context.scene.objects if obj.type == 'ARMATURE'
               for bone in obj.pose.bones if bone.custom_shape}
    objects = [obj for obj in bpy.context.scene.objects if obj not in widgets]
    for widget in widgets:
        bpy.data.objects.remove(widget, do_unlink=True)
    meshes = [obj for obj in objects if obj.type == 'MESH']
    rigs = [obj for obj in objects if obj.type == 'ARMATURE']
    if any(obj.animation_data and (obj.animation_data.action or obj.animation_data.nla_tracks)
           for obj in objects):
        raise ValueError('Animated inputs are not supported by bind-pose normalization; supply an unanimated rig')
    if not meshes or not any(len(obj.data.polygons) for obj in meshes):
        raise ValueError('Input contains no polygon mesh')
    if args.require_rig and not rigs:
        raise ValueError('A rig was required but no armature was imported')
    warnings = report['warnings']
    for obj in meshes:
        if not obj.data.materials:
            warnings.append(f'{obj.name}: no material slots')
        if not obj.data.uv_layers:
            warnings.append(f'{obj.name}: no UV map')
        modifiers = [m for m in obj.modifiers if m.type == 'ARMATURE' and m.object]
        if args.require_rig and not modifiers:
            raise ValueError(f'{obj.name}: no armature modifier')
        for modifier in modifiers:
            bone_names = {b.name for b in modifier.object.data.bones if b.use_deform}
            groups = {g.index for g in obj.vertex_groups if g.name in bone_names}
            unweighted = sum(not any(g.group in groups and g.weight > 0 for g in v.groups)
                             for v in obj.data.vertices)
            if unweighted:
                raise ValueError(f'{obj.name}: {unweighted} vertices have no deform-bone weight')
            overweight = sum(sum(g.weight > 0 and g.group in groups for g in v.groups) > 4
                             for v in obj.data.vertices)
            if overweight:
                warnings.append(f'{obj.name}: {overweight} vertices exceed four influences; all exported')
    materials = {slot.material for obj in meshes for slot in obj.material_slots if slot.material}
    textures = set()
    for material in materials:
        if material.use_nodes:
            for node in material.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    image = node.image
                    if image.size[0] == 0 or image.size[1] == 0:
                        raise ValueError(f'Missing or unreadable texture: {image.name}')
                    textures.add(image.name)
        else:
            warnings.append(f'{material.name}: non-node material; verify exported appearance')
    # A shared parent changes mesh and skeleton coordinates together. Applying transforms
    # to skinned mesh data independently would corrupt bind matrices and animations.
    root = bpy.data.objects.new(args.name + '_Normalization', None)
    bpy.context.collection.objects.link(root)
    for obj in objects:
        if obj.parent is None:
            obj.parent = root
            obj.matrix_parent_inverse = Matrix.Identity(4)
    root.rotation_euler.z = math.radians(args.yaw_degrees)
    bpy.context.view_layer.update()
    low, high = bounds(meshes)
    height = high[2] - low[2]
    if not math.isfinite(height) or height <= 1e-8:
        raise ValueError('Mesh has zero or invalid vertical height')
    scale = args.height_m / height
    root.scale = (scale,) * 3
    root.location = (-scale * (low[0] + high[0]) / 2,
                     -scale * (low[1] + high[1]) / 2, -scale * low[2])
    bpy.context.scene.unit_settings.system = 'METRIC'
    bpy.context.scene.unit_settings.scale_length = 1.0
    bpy.context.view_layer.update()
    low, high = bounds(meshes)
    # UE Interchange can multiply a skinned mesh parent scale twice. Bake the same
    # world normalization into BOTH mesh vertices and armature rest bones, then
    # export identity object transforms. Bone hierarchy and weights stay intact.
    transforms = {obj: obj.matrix_world.copy() for obj in meshes + rigs}
    for obj, transform in transforms.items():
        obj.data = obj.data.copy()
        if obj.type == 'MESH':
            obj.data.transform(transform, shape_keys=True)
        else:
            obj.data.transform(transform)
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
    for obj in list(bpy.context.scene.objects):
        if obj not in transforms:
            bpy.data.objects.remove(obj, do_unlink=True)
    for obj in meshes:
        skin = next((m for m in obj.modifiers if m.type == 'ARMATURE' and m.object), None)
        if skin:
            obj.parent = skin.object
            obj.matrix_parent_inverse = Matrix.Identity(4)
    bpy.context.view_layer.update()
    low, high = bounds(meshes)
    if abs(high[2] - low[2] - args.height_m) > max(1e-5, args.height_m * 1e-5):
        raise ValueError('Baking changed the normalized mesh height; unsupported rig transforms')
    target.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.file.pack_all()
    bpy.ops.wm.save_as_mainfile(filepath=str(target.with_suffix('.blend')))
    result = bpy.ops.export_scene.gltf(
        filepath=str(target), export_format='GLB', export_yup=True,
        export_animations=True, export_skins=True, export_all_influences=True,
        export_materials='EXPORT', export_cameras=False, export_lights=False)
    if 'FINISHED' not in result or not target.is_file():
        raise RuntimeError('GLB export did not complete')
    report.update(success=True, height_m=high[2] - low[2],
                  meshes=[{'name': obj.name, 'vertices': len(obj.data.vertices),
                           'polygons': len(obj.data.polygons)} for obj in meshes],
                  materials=sorted(m.name for m in materials), textures=sorted(textures),
                  bones={obj.name: [b.name for b in obj.data.bones] for obj in rigs},
                  bounds_blender_m={'min': low, 'max': high},
                  normalization='World transform baked into mesh vertices and armature rest bones',
                  output=str(target), blend=str(target.with_suffix('.blend')))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ('input', 'output', 'report', 'name'):
        parser.add_argument('--' + flag, required=True)
    parser.add_argument('--height-m', type=float, default=1.8)
    parser.add_argument('--yaw-degrees', type=float, default=0)
    parser.add_argument('--require-rig', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    report = {'schema': 'tripo.blender.v1', 'success': False, 'warnings': [],
              'height_m': None, 'meshes': [], 'materials': [], 'bones': {},
              'coordinate_system': {'glb': 'right-handed Y-up, meters',
                                    'blend': 'right-handed Z-up, meters',
                                    'origin': 'grounded at mesh bounds, horizontally centered'}}
    try:
        process(args, report)
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        destination = Path(args.report)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
