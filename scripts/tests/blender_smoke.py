# Copyright (C) 2026 - Joseph M Wilcox
"""Real Blender fixture/export/process/import test; invoke with Python --blender EXE."""
import argparse
import json
import math
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def blender_phase(phase, directory):
    import bpy
    from mathutils import Vector
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if phase == 'fixture':
        bpy.ops.mesh.primitive_cube_add(location=(3, -2, 5))
        mesh = bpy.context.object
        mesh.name = 'FixtureBody'
        mesh.scale = (0.5, 0.25, 2)
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
        material = bpy.data.materials.new('FixturePBR')
        material.use_nodes = True
        shader = material.node_tree.nodes.get('Principled BSDF')
        shader.inputs['Metallic'].default_value = 0.25
        shader.inputs['Roughness'].default_value = 0.65
        image = bpy.data.images.new('FixtureTexture', width=2, height=2)
        image.pixels[:] = [0.1, 0.6, 0.8, 1.0] * 4
        image.pack()
        node = material.node_tree.nodes.new('ShaderNodeTexImage')
        node.image = image
        material.node_tree.links.new(node.outputs['Color'], shader.inputs['Base Color'])
        mesh.data.materials.append(material)
        bpy.ops.object.armature_add(location=(3, -2, 3))
        rig = bpy.context.object
        rig.name = 'FixtureRig'
        rig.data.bones[0].name = 'RootBone'
        bpy.ops.object.mode_set(mode='EDIT')
        root = rig.data.edit_bones['RootBone']
        root.tail = (0, 0, 2)
        child = rig.data.edit_bones.new('UpperBone')
        child.head, child.tail, child.parent = (0, 0, 2), (0, 0, 4), root
        bpy.ops.object.mode_set(mode='OBJECT')
        group = mesh.vertex_groups.new(name='RootBone')
        group.add([v.index for v in mesh.data.vertices if v.co.z < 0], 1, 'REPLACE')
        group = mesh.vertex_groups.new(name='UpperBone')
        group.add([v.index for v in mesh.data.vertices if v.co.z >= 0], 1, 'REPLACE')
        modifier = mesh.modifiers.new('Skin', 'ARMATURE')
        modifier.object = rig
        world = mesh.matrix_world.copy()
        mesh.parent = rig
        mesh.matrix_world = world
        bpy.ops.export_scene.gltf(filepath=str(directory / 'fixture.glb'), export_format='GLB')
        image.filepath_raw = str(directory / 'fixture.png')
        image.file_format = 'PNG'
        image.save()
        bpy.ops.export_scene.fbx(filepath=str(directory / 'fixture.fbx'),
                                 path_mode='COPY', embed_textures=True,
                                 add_leaf_bones=False, bake_anim=False)
    else:
        bpy.ops.import_scene.gltf(filepath=str(directory / 'canonical.glb'))
        rigs = [o for o in bpy.context.scene.objects if o.type == 'ARMATURE']
        widgets = {b.custom_shape for r in rigs for b in r.pose.bones if b.custom_shape}
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH' and o not in widgets]
        assert len(meshes) == 1 and len(rigs) == 1
        assert {b.name for b in rigs[0].data.bones} == {'RootBone', 'UpperBone'}
        depsgraph = bpy.context.evaluated_depsgraph_get()
        obj = meshes[0].evaluated_get(depsgraph)
        points = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        low, high = min(p.z for p in points), max(p.z for p in points)
        assert abs(low) < 1e-5 and abs(high - 1.8) < 1e-5, (low, high)
        assert len(meshes[0].data.uv_layers) > 0
        assert any(m.type == 'ARMATURE' for m in meshes[0].modifiers)
        assert all(any(g.weight > 0 for g in v.groups) for v in meshes[0].data.vertices)
        shader = meshes[0].data.materials[0].node_tree.nodes.get('Principled BSDF')
        assert abs(shader.inputs['Metallic'].default_value - .25) < 1e-5
        assert abs(shader.inputs['Roughness'].default_value - .65) < 1e-5
        assert any(n.type == 'TEX_IMAGE' and n.image.size[0] == 2
                   for n in meshes[0].data.materials[0].node_tree.nodes)
        # An actual pose must deform the round-tripped skinned mesh.
        before = [obj.matrix_world @ v.co for v in obj.data.vertices]
        rigs[0].pose.bones['UpperBone'].rotation_mode = 'XYZ'
        rigs[0].pose.bones['UpperBone'].rotation_euler.y = math.radians(30)
        bpy.context.view_layer.update()
        obj = meshes[0].evaluated_get(bpy.context.evaluated_depsgraph_get())
        after = [obj.matrix_world @ v.co for v in obj.data.vertices]
        assert max((a - b).length for a, b in zip(before, after)) > .05
        print('BLENDER_ROUNDTRIP_PASS')


def main():
    if '--phase' in sys.argv:
        args = sys.argv[sys.argv.index('--') + 1:]
        blender_phase(args[1], Path(args[2]))
        return
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--blender', required=True)
    parser.add_argument('--work-dir')
    args = parser.parse_args()
    directory = Path(args.work_dir or tempfile.mkdtemp(prefix='tripo-blender-')).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    def run(script_path, *arguments):
        subprocess.run([args.blender, '--background', '--factory-startup', '--python-exit-code', '1',
                        '--python', str(script_path), '--', *map(str, arguments)], check=True)
    run(script, '--phase', 'fixture', directory)
    for extension in ('glb', 'fbx'):
        out = directory if extension == 'glb' else directory / 'fbx'
        run(script.parent.parent / 'blender' / 'process.py', '--input', directory / ('fixture.' + extension),
            '--output', out / 'canonical.glb', '--report', out / 'report.json',
            '--name', 'Smoke', '--height-m', '1.8', '--require-rig', '--yaw-degrees', '90')
        report = json.loads((out / 'report.json').read_text())
        assert report['success'] and report['schema'] == 'tripo.blender.v1'
        assert (out / 'canonical.blend').is_file()
        data = (out / 'canonical.glb').read_bytes()
        length, kind = struct.unpack_from('<II', data, 12)
        assert kind == 0x4E4F534A
        gltf = json.loads(data[20:20 + length])
        assert all('uri' not in b for b in gltf.get('buffers', []))
        assert all('bufferView' in image for image in gltf.get('images', []))
        assert all(all(abs(v - 1) < 1e-6 for v in node.get('scale', [1, 1, 1]))
                   for node in gltf['nodes'])
        run(script, '--phase', 'verify', out)
    print('Smoke test passed:', directory)


if __name__ == '__main__':
    main()
