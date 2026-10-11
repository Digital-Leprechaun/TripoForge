# Copyright (C) 2026 - Joseph M Wilcox
"""Apply a sampled Unreal component-space pose to a converted Blender rig for visual QA."""
import argparse,json,sys,math
from pathlib import Path
import bpy
from mathutils import Matrix,Quaternion,Vector
p=argparse.ArgumentParser();p.add_argument('--blend',required=True);p.add_argument('--pose',required=True);p.add_argument('--output',required=True)
a=p.parse_args(sys.argv[sys.argv.index('--')+1:])
bpy.ops.wm.open_mainfile(filepath=str(Path(a.blend).resolve()))
rig=next(o for o in bpy.context.scene.objects if o.type=='ARMATURE')
data=json.loads(Path(a.pose).read_text(encoding='utf-8-sig'))
pose=data.get('sampled_pose_world_cm',data)
flip=Matrix.Diagonal((1,-1,1,1));transport=Matrix.Rotation(math.pi/2,4,'X')
def depth(b):return 1+depth(b.parent) if b.parent else 0
for bone in sorted(rig.pose.bones,key=depth):
 t=pose[bone.name];x,y,z,w=t['rotation_xyzw']
 m=Quaternion((w,x,y,z)).to_matrix().to_4x4();m.translation=Vector(t['translation'])*.01
 # Reference GLB uses the same explicit per-bone transport basis.
 scale=t.get('scale',[1,1,1]);m=m@Matrix.Diagonal((*scale,1))
 bone.matrix=flip@m@flip@transport
 bpy.context.view_layer.update()
bpy.ops.wm.save_as_mainfile(filepath=str(Path(a.output).resolve()))
