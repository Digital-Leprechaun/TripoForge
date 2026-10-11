# Copyright (C) 2026 - Joseph M Wilcox
"""Offline Blender regression: equivalent quaternion signs must not fail axes validation."""
import math
from pathlib import Path
import sys
from mathutils import Quaternion

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'blender'))
from target_rig import rotation_error

q=Quaternion((0.3,0.5,0.7),math.pi)
opposite=Quaternion(tuple(-x for x in q))
assert rotation_error(q,q)<1e-6
assert rotation_error(q,opposite)<1e-6
# Actual ik_hand_l quaternions from the failed Zombie_01 build.
actual=Quaternion((1.2186777667011484e-6,-.45776763558387756,-.6113659143447876,-.6455079913139343))
reference=Quaternion((7.312063843301075e-8,.45776620507240295,.6113661527633667,.6455088257789612))
assert actual.rotation_difference(reference).angle>6
assert rotation_error(actual,reference)<1e-3
for angle in (.0005,.002,.5,math.pi):
 changed=q@Quaternion((1,0,0),angle)
 error=rotation_error(q,changed)
 assert abs(error-angle)<1e-6,(error,angle)
 assert (error>1e-3)==(angle>1e-3)
 assert abs(rotation_error(q,Quaternion(tuple(-x for x in changed)))-angle)<1e-6
print('TARGET_RIG_ROTATION_OK')
