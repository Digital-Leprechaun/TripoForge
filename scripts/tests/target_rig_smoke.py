# Copyright (C) 2026 - Joseph M Wilcox
"""Blender deformation smoke for an explicit humanoid target-rig output, offline."""
import argparse,json,math,sys
from pathlib import Path
import bpy
from mathutils import Quaternion
p=argparse.ArgumentParser();p.add_argument('--blend',required=True);p.add_argument('--report',required=True)
p.add_argument('--reference',default=str(Path(__file__).resolve().parents[2]/'Converters'/'Mixamo2UE5'/'reference.blend'))
p.add_argument('--height-m',type=float)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'blender'))
from target_rig import load,bake_world,bounds,rotation_error
bpy.ops.wm.read_factory_settings(use_empty=True)
reference,reference_meshes,_=load(a.reference)
bake_world(reference,reference_meshes)
expected={b.name:(b.parent.name if b.parent else None,
 (b.parent.matrix_local.inverted()@b.matrix_local if b.parent else b.matrix_local).to_quaternion()) for b in reference.data.bones}
bpy.ops.wm.open_mainfile(filepath=str(Path(a.blend).resolve()))
rig=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
meshes=[o for o in bpy.context.scene.objects if o.type=='MESH' and any(m.type=='ARMATURE' and m.object==rig for m in o.modifiers)]
assert set(rig.data.bones.keys())==set(expected)
for bone in rig.data.bones:
 parent,orientation=expected[bone.name]
 assert (bone.parent.name if bone.parent else None)==parent
 local=bone.parent.matrix_local.inverted()@bone.matrix_local if bone.parent else bone.matrix_local
 assert rotation_error(local.to_quaternion(),orientation)<1e-3,bone.name
assert all(abs(x-1)<1e-5 for x in rig.scale)
low,high=bounds(meshes)
if a.height_m is not None:assert abs(high.z-low.z-a.height_m)<1e-5
for mesh in meshes:
 for v in mesh.data.vertices:
  assert 0<len(v.groups)<=8
  assert abs(sum(g.weight for g in v.groups)-1)<1e-5
  assert all(mesh.vertex_groups[g.group].name in rig.data.bones for g in v.groups)
def points():
 bpy.context.view_layer.update();dg=bpy.context.evaluated_depsgraph_get()
 return [m.matrix_world@v.co for m in [o.evaluated_get(dg) for o in meshes] for v in m.data.vertices]
original=points();result={}
for name in ['lowerarm_l','calf_r','index_01_l','thumb_01_r']:
 bone=rig.pose.bones[name];bone.rotation_mode='QUATERNION';bone.rotation_quaternion=Quaternion((1,0,0),.6)
 after=points();distances=[(x-y).length for x,y in zip(original,after)]
 assert all(math.isfinite(d) for d in distances)
 moved=sum(d>.0001 for d in distances)
 assert moved>0,(name,'no weighted deformation')
 result[name]={'moved_vertices':moved,'max_displacement_m':max(distances)}
 bone.rotation_quaternion=Quaternion()
path=Path(a.report).resolve();path.parent.mkdir(parents=True,exist_ok=True)
path.write_text(json.dumps({'success':True,'bones':len(expected),'height_m':high.z-low.z,'vertices':len(original),'deformation':result},indent=2))
print('TARGET_RIG_SMOKE_OK',result)
