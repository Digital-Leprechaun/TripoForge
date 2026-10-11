# Copyright (C) 2026 - Joseph M Wilcox
"""Build a private weighted reference from Unreal FBX plus exported reference matrices.

Run in Blender with --fbx --contract --output. No Unreal project is modified.
"""
import argparse
import hashlib
import math
import json
from pathlib import Path
import sys
import bpy
from mathutils import Matrix, Quaternion, Vector


def main():
    p=argparse.ArgumentParser()
    for name in ['fbx','contract','output']:p.add_argument('--'+name,required=True)
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    data=json.loads(Path(args.contract).read_text(encoding='utf-8-sig'))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.fbx(filepath=str(Path(args.fbx).resolve()),automatic_bone_orientation=False)
    rigs=[o for o in bpy.context.scene.objects if o.type=='ARMATURE']
    if len(rigs)!=1:raise ValueError('Need one reference armature')
    arm=rigs[0]
    meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==arm for m in o.modifiers)]
    if not meshes:raise ValueError('Need weighted reference mesh')
    world={m:m.matrix_world.copy() for m in meshes}
    for mesh in meshes:
        mesh.parent=None;mesh.data.transform(world[mesh]);mesh.matrix_world=Matrix.Identity(4)
    arm.parent=None;arm.matrix_world=Matrix.Identity(4)
    bpy.context.view_layer.objects.active=arm;bpy.ops.object.mode_set(mode='EDIT')
    expected={b['name'] for b in data['bones']}
    for bone in list(arm.data.edit_bones):
        if bone.name not in expected:arm.data.edit_bones.remove(bone)
    flip=Matrix.Diagonal((1,-1,1,1))
    for spec in data['bones']:
        bone=arm.data.edit_bones.get(spec['name']) or arm.data.edit_bones.new(spec['name'])
        t=spec['world'];x,y,z,w=t['rotation_xyzw']
        mat=Quaternion((w,x,y,z)).to_matrix().to_4x4();mat.translation=Vector(t['translation'])*.01
        bone.use_connect=False;bone.head=(0,0,0);bone.tail=(0,.04,0);bone.matrix=flip@mat@flip@Matrix.Rotation(math.pi/2,4,'X');bone.length=.04
    for spec in data['bones']:
        arm.data.edit_bones[spec['name']].parent=arm.data.edit_bones.get(spec['parent'])
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in arm.pose.bones:bone.matrix_basis=Matrix.Identity(4)
    arm.animation_data_clear();arm.name='Armature'
    for mesh in meshes:mesh.parent=arm;mesh.matrix_parent_inverse=Matrix.Identity(4)
    bpy.context.scene.unit_settings.system='METRIC';bpy.context.scene.unit_settings.scale_length=1
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output))
    metadata={'source_mesh':data.get('mesh'),'source_skeleton':data.get('skeleton'),'bone_count':len(expected),
              'contract_sha256':hashlib.sha256(Path(args.contract).read_bytes()).hexdigest(),
              'fbx_sha256':hashlib.sha256(Path(args.fbx).read_bytes()).hexdigest(),
              'basis':'Unreal left handed Z-up cm to Blender right handed Z-up meters: diag(1,-1,1)',
              'root':'explicit bone from contract, including FBX armature-object implicit root'}
    output.with_suffix('.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')

if __name__=='__main__':main()
