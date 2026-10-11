# Copyright (C) 2026 - Joseph M Wilcox
"""Bind new geometry to an explicit weighted reference rig; run inside Blender.

This changes geometry and weights. It never claims a bone rename preserves animation compatibility.
"""
import argparse
import json
import hashlib
import math
from pathlib import Path
import sys
import traceback

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

MIXAMO = {'Root':'root', 'Hips':'pelvis', 'Spine':'spine_01', 'Spine1':'spine_02',
          'Spine2':'spine_03', 'Neck':'neck_01', 'Head':'head'}
for prefix, side in [('Left','l'), ('Right','r')]:
    for source, target in [('Shoulder','clavicle'),('Arm','upperarm'),('ForeArm','lowerarm'),('Hand','hand'),
                           ('UpLeg','thigh'),('Leg','calf'),('Foot','foot'),('ToeBase','ball')]:
        MIXAMO[prefix+source] = target+'_'+side
SUCCESSORS = {'root':'pelvis','pelvis':'spine_01','spine_01':'spine_02','spine_02':'spine_03',
              'spine_03':'neck_01','neck_01':'head'}
for side in ['l','r']:
    for a,b in [('clavicle','upperarm'),('upperarm','lowerarm'),('lowerarm','hand'),
                ('thigh','calf'),('calf','foot'),('foot','ball')]:
        SUCCESSORS[a+'_'+side] = b+'_'+side


def load(path):
    before = set(bpy.data.objects)
    suffix = Path(path).suffix.lower()
    if suffix == '.fbx':
        bpy.ops.import_scene.fbx(filepath=str(path), automatic_bone_orientation=False)
    elif suffix == '.glb':
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == '.blend':
        with bpy.data.libraries.load(str(path), link=False) as (src,dst):
            dst.objects = src.objects
        for obj in dst.objects:
            bpy.context.scene.collection.objects.link(obj)
    else:
        raise ValueError('Reference/input must be FBX, GLB or BLEND')
    objs = set(bpy.data.objects) - before
    rigs = [o for o in objs if o.type == 'ARMATURE']
    if len(rigs) != 1:
        raise ValueError('Expected exactly one armature: '+str(path))
    meshes = [o for o in objs if o.type == 'MESH' and any(m.type == 'ARMATURE' and m.object == rigs[0] for m in o.modifiers)]
    if not meshes:
        raise ValueError('Reference/input must contain weighted mesh geometry')
    return rigs[0], meshes, objs


def bake_world(arm, meshes):
    transforms = {o:o.matrix_world.copy() for o in [arm]+meshes}
    for obj in [arm]+meshes:
        obj.parent = None
    arm.data.transform(transforms[arm])
    arm.matrix_world = Matrix.Identity(4)
    for mesh in meshes:
        mesh.data.transform(transforms[mesh])
        mesh.matrix_world = Matrix.Identity(4)
    for bone in arm.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    arm.animation_data_clear()
    bpy.context.view_layer.update()


def bounds(meshes):
    pts = [v.co for m in meshes for v in m.data.vertices]
    return Vector(tuple(min(p[i] for p in pts) for i in range(3))), Vector(tuple(max(p[i] for p in pts) for i in range(3)))


def weights(mesh, vertex):
    return {mesh.vertex_groups[g.group].name:g.weight for g in vertex.groups if g.weight > 1e-8}


def region(name):
    # Mannequins may weight the neck surface entirely to the head.
    if name == 'head' or name.startswith('neck_'):
        return 'head_neck'
    if name.startswith(('thumb_','index_','middle_','ring_','pinky_')):
        return 'hand_'+name[-1]
    return name.replace('_twist_01','').replace('_twist_02','')


def rotation_error(actual, expected):
    """Shortest orientation difference; q and -q describe the same rotation."""
    delta = actual.normalized().rotation_difference(expected.normalized()).normalized()
    # atan2 retains precision near zero, unlike acos of a rounded dot product.
    return 2 * math.atan2(math.sqrt(delta.x**2 + delta.y**2 + delta.z**2), abs(delta.w))


def fit_source(source, target, meshes, reference):
    mapping = {}
    for bone in source.data.bones:
        candidate = bone.name if bone.name in target.data.bones else MIXAMO.get(bone.name.split(':')[-1])
        if candidate in target.data.bones:
            mapping[bone.name] = candidate
    reverse = {v:k for k,v in mapping.items()}
    required = {'root','pelvis','head','thigh_l','thigh_r','upperarm_l','upperarm_r'}
    if len(mapping)<20 or not required.issubset(reverse):
        raise ValueError('Humanoid fitter requires mapped UE-style root/pelvis/head/limb names; provide a compatible reference')
    low,high=bounds(meshes);ref_low,ref_high=bounds(reference)
    global_scale=(ref_high.z-ref_low.z)/(high.z-low.z)
    src_across=source.data.bones[reverse['thigh_l']].head_local-source.data.bones[reverse['thigh_r']].head_local
    dst_across=target.data.bones['thigh_l'].head_local-target.data.bones['thigh_r'].head_local
    yaw=math.atan2(dst_across.y,dst_across.x)-math.atan2(src_across.y,src_across.x)
    rigid=Matrix.Rotation(yaw,4,'Z') @ Matrix.Scale(global_scale,4)
    source.data.transform(rigid)
    for mesh in meshes:mesh.data.transform(rigid)
    original={b.name:b.matrix_local.copy() for b in source.data.bones}
    old_target={b.name:b.matrix_local.copy() for b in target.data.bones}
    local_ref={b.name:(b.parent.matrix_local.inverted()@b.matrix_local if b.parent else b.matrix_local.copy()) for b in target.data.bones}
    ratios={}
    for name,child in SUCCESSORS.items():
        if name in reverse and child in reverse:
            old=(old_target[child].translation-old_target[name].translation).length
            new=(original[reverse[child]].translation-original[reverse[name]].translation).length
            ratios[name]=new/old if old>1e-8 else 1
    def inherited_ratio(name):
        if name in ratios:return ratios[name]
        b=target.data.bones[name]
        return inherited_ratio(b.parent.name) if b.parent else 1
    desired={}
    def solve(name):
        if name in desired:return desired[name]
        bone=target.data.bones[name];local=local_ref[name].copy()
        if bone.parent:
            parent=bone.parent.name
            if name in reverse and parent in reverse:
                new_length=(original[reverse[name]].translation-original[reverse[parent]].translation).length
                if local.translation.length>1e-8:local.translation=local.translation.normalized()*new_length
            else:
                local.translation*=inherited_ratio(parent)
            desired[name]=solve(parent)@local
        else:desired[name]=local
        return desired[name]
    for name in old_target:solve(name)
    # Helpers follow their corresponding anatomical endpoints. Preserve their local rotations.
    for name,match in [('ik_foot_l','foot_l'),('ik_foot_r','foot_r'),('ik_hand_gun','hand_r'),
                       ('ik_hand_l','hand_l'),('ik_hand_r','hand_r'),('weapon_l','hand_l'),('weapon_r','hand_r')]:
        if name in desired:desired[name].translation=desired[match].translation.copy()
    swings={}
    def swing(new):
        if new in swings:return swings[new]
        old=reverse[new];child=SUCCESSORS.get(new)
        if child in reverse:
            a=original[reverse[child]].translation-original[old].translation
            b=desired[child].translation-desired[new].translation
            q=a.normalized().rotation_difference(b.normalized()) if min(a.length,b.length)>1e-8 else Matrix.Identity(3).to_quaternion()
        else:
            parent=source.data.bones[old].parent
            q=swing(mapping[parent.name]) if parent and parent.name in mapping else Matrix.Identity(3).to_quaternion()
        swings[new]=q
        return q
    transforms={old:Matrix.Translation(desired[new].translation)@swing(new).to_matrix().to_4x4()@Matrix.Translation(-original[old].translation) for old,new in mapping.items()}
    original_weights={}
    for mesh in meshes:
        original_weights[mesh.name]=[]
        for v in mesh.data.vertices:
            available={k:w for k,w in weights(mesh,v).items() if k in transforms};total=sum(available.values())
            if total<.99:raise ValueError('Unmapped/unweighted source vertex')
            original_weights[mesh.name].append({mapping[k]:w/total for k,w in available.items()})
            p=v.co.copy();v.co=sum(((transforms[k]@p)*(w/total) for k,w in available.items()),Vector())
    donor_delta={n:desired[n]@old_target[n].inverted() for n in desired}
    for mesh in reference:
        for v in mesh.data.vertices:
            w={n:w for n,w in weights(mesh,v).items() if n in donor_delta};total=sum(w.values())
            if total>0:
                p=v.co.copy();v.co=sum(((donor_delta[n]@p)*(value/total) for n,value in w.items()),Vector())
    # Ground geometry while retaining the animation root and IK root origins.
    mesh_low,mesh_high=bounds(meshes)
    ground_shift=Vector((0,0,-mesh_low.z))
    for name in desired:
        if name not in {'root','ik_foot_root','ik_hand_root'}:
            desired[name].translation+=ground_shift
    for mesh in meshes+reference:
        for vertex in mesh.data.vertices:
            vertex.co+=ground_shift
    bpy.context.view_layer.objects.active=target
    bpy.ops.object.mode_set(mode='EDIT')
    for bone in target.data.edit_bones:
        bone.use_connect=False
        length=bone.length;bone.matrix=desired[bone.name];bone.length=length*inherited_ratio(bone.name)
    bpy.ops.object.mode_set(mode='OBJECT')
    for bone in target.pose.bones:bone.matrix_basis=Matrix.Identity(4)
    bpy.context.view_layer.update()
    for bone in target.data.bones:
        local=bone.parent.matrix_local.inverted()@bone.matrix_local if bone.parent else bone.matrix_local
        error=rotation_error(local.to_quaternion(),local_ref[bone.name].to_quaternion())
        if error>1e-3:raise ValueError('Target local axes changed: %s (%.6f radians)' % (bone.name,error))
    return mapping,original_weights,global_scale


def reference_data(meshes, target):
    points,triangles,skin = [],[],[]
    for mesh in meshes:
        offset = len(points)
        points.extend(v.co.copy() for v in mesh.data.vertices)
        skin.extend({n:w for n,w in weights(mesh,v).items() if n in target.data.bones} for v in mesh.data.vertices)
        mesh.data.calc_loop_triangles()
        triangles.extend(tuple(offset+i for i in t.vertices) for t in mesh.data.loop_triangles)
    by_region = {}
    for tri in triangles:
        regions = set()
        for i in tri:
            sums = {}
            for name,w in skin[i].items():
                key = region(name)
                sums[key] = sums.get(key,0)+w
            regions.update(n for n,w in sums.items() if w > .2)
        for key in regions:
            by_region.setdefault(key,[]).append(tri)
    return points, skin, {k:(BVHTree.FromPolygons(points,tris,all_triangles=True),tris) for k,tris in by_region.items()}


def interpolate(point, ids, points, skin):
    a,b,c = [points[i] for i in ids]
    e0,e1,d = b-a,c-a,point-a
    d00,d01,d11,d20,d21 = e0.dot(e0),e0.dot(e1),e1.dot(e1),d.dot(e0),d.dot(e1)
    denom = d00*d11-d01*d01
    if abs(denom) < 1e-16:
        ratios = [1,0,0]
    else:
        y,z = (d11*d20-d01*d21)/denom,(d00*d21-d01*d20)/denom
        ratios = [max(0,1-y-z),max(0,y),max(0,z)]
    total = sum(ratios)
    result = {}
    for idx,ratio in zip(ids,ratios):
        for name,w in skin[idx].items():
            result[name] = result.get(name,0)+w*ratio/total
    return result


def transfer(meshes, reference, target, original, max_distance_ratio):
    points,skin,trees = reference_data(reference,target)
    low,high = bounds(reference)
    limit = (high.z-low.z)*max_distance_ratio
    distances = []
    region_distances = {}
    for mesh in meshes:
        for group in list(mesh.vertex_groups):
            mesh.vertex_groups.remove(group)
        for bone in target.data.bones:
            mesh.vertex_groups.new(name=bone.name)
        for v in mesh.data.vertices:
            labels = [region(n) for n,w in original[mesh.name][v.index].items() if w > .15]
            candidates = []
            for label in labels:
                if label not in trees:
                    continue
                tree,tris = trees[label]
                found = tree.find_nearest(v.co)
                if found[0] is not None:
                    candidates.append((found[3],found[0],tris[found[2]],label))
            if not candidates:
                raise ValueError(f'No reference surface for {labels} at {mesh.name}:{v.index}; available regions: {sorted(trees)}')
            distance,point,ids,label = min(candidates,key=lambda x:x[0])
            region_distances.setdefault(label,[]).append(distance)
            distances.append(distance)
            if distance > limit:
                raise ValueError('Reference fit exceeds distance limit: %.4fm > %.4fm at %s:%d' % (distance,limit,mesh.name,v.index))
            result = interpolate(point,ids,points,skin)
            result = sorted(((n,w) for n,w in result.items() if w>1e-6),key=lambda x:-x[1])[:8]
            total = sum(w for n,w in result)
            if total <= 0:
                raise ValueError('Reference surface has no skin weights')
            for name,w in result:
                mesh.vertex_groups[name].add([v.index],w/total,'REPLACE')
        for mod in list(mesh.modifiers):
            if mod.type == 'ARMATURE':
                mesh.modifiers.remove(mod)
        mod = mesh.modifiers.new('TargetRig','ARMATURE')
        mod.object = target
        mesh.parent = target
        mesh.matrix_parent_inverse = Matrix.Identity(4)
    distances.sort()
    return {'max_m':max(distances),'p95_m':distances[int((len(distances)-1)*.95)],'mean_m':sum(distances)/len(distances),
            'limit_m':limit,'vertices':len(distances),'regions':{k:{'max_m':max(v),'mean_m':sum(v)/len(v),'vertices':len(v)} for k,v in region_distances.items()},'method':'anatomical-region nearest triangle barycentric, max8influences'}


def run(args):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    source,meshes,source_objects = load(Path(args.input).resolve())
    target,reference,reference_objects = load(Path(args.reference).resolve())
    bake_world(source,meshes)
    bake_world(target,reference)
    expected = {b.name:{'parent':b.parent.name if b.parent else None,'matrix':b.matrix_local.copy()} for b in target.data.bones}
    original_uvs = {m.name:[(u.name,[(float(x.uv.x),float(x.uv.y)) for x in u.data]) for u in m.data.uv_layers] for m in meshes}
    reference_low,reference_high = bounds(reference)
    source_low,source_high = bounds(meshes)
    mapping,old_weights,scale = fit_source(source,target,meshes,reference)
    # Rest-pose fitting can change the mesh bounds. Preserve the already normalized
    # source height, scaling the skeleton and donor surface together with the mesh.
    fitted_low,fitted_high = bounds(meshes)
    height_scale = (source_high.z-source_low.z)/(fitted_high.z-fitted_low.z)
    height_transform = Matrix.Scale(height_scale,4)
    target.data.transform(height_transform)
    for mesh in meshes+reference:
        mesh.data.transform(height_transform)
    bpy.context.view_layer.update()
    distances = transfer(meshes,reference,target,old_weights,args.max_distance_ratio)
    for obj in source_objects|reference_objects:
        if obj not in meshes and obj != target:
            bpy.data.objects.remove(obj,do_unlink=True)
    target.name = 'Armature'
    bpy.context.view_layer.update()
    for mesh in meshes:
        assert original_uvs[mesh.name] == [(u.name,[(float(x.uv.x),float(x.uv.y)) for x in u.data]) for u in mesh.data.uv_layers]
        for v in mesh.data.vertices:
            assert abs(sum(g.weight for g in v.groups)-1)<1e-5
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    bpy.ops.object.select_all(action='DESELECT');target.select_set(True)
    for mesh in meshes:mesh.select_set(True)
    bpy.context.view_layer.objects.active=target
    bpy.ops.wm.save_as_mainfile(filepath=str(output.with_suffix('.blend')))
    bpy.ops.export_scene.gltf(filepath=str(output),export_format='GLB',use_selection=True,
        export_animations=False,export_def_bones=False,export_skins=True,export_yup=True,export_all_influences=True)
    low,high=bounds(meshes)
    return {'schema':'tripo.target-rig.v1','success':True,'profile':args.profile,'reference':str(Path(args.reference).resolve()),
            'output':str(output),'height_m':high.z-low.z,'bounds_m':{'min':list(low),'max':list(high)},'reference_height_m':reference_high.z-reference_low.z,'source_height_m':source_high.z-source_low.z,'reference_sha256':hashlib.sha256(Path(args.reference).read_bytes()).hexdigest(),'bone_count':len(expected),'bones':{n:v['parent'] for n,v in expected.items()},
            'source_mapping':mapping,'fit_global_scale':scale,'fit_height_scale':height_scale,'weight_transfer':distances,
            'weights_preserved':False,'uvs_preserved':True,'reference_local_axes_preserved_in_blender':True,'reference_translations':'adapted to source segment lengths',
            'animation_compatibility':'requires Unreal bind+animation validation',
            'warnings':['Geometry and skin weights changed; review fingers, shoulders, armor and weapon grip before production use.']}


def main():
    parser=argparse.ArgumentParser()
    for name in ['input','reference','output','report']:parser.add_argument('--'+name,required=True)
    parser.add_argument('--profile',choices=['ue5','custom'],default='custom')
    parser.add_argument('--weights',choices=['reference'],default='reference')
    parser.add_argument('--max-distance-ratio',type=float,default=.15)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    report={'success':False}
    try:
        if not math.isfinite(args.max_distance_ratio) or not 0<args.max_distance_ratio<=.5:raise ValueError('Invalid distance limit')
        report=run(args)
    except Exception as exc:
        report['error']=str(exc)
        traceback.print_exc()
        raise
    finally:
        path=Path(args.report).resolve();path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':main()
