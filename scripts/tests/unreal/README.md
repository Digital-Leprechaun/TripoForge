<!-- Copyright (C) 2026 - Joseph M Wilcox -->
# Exact reference skeleton and animation test

`setup.ps1` builds an isolated editor project with the validation-only C++ helper
and copies the supplied reference assets read-only from a supplied project. It does not edit
the reference project. Include any asset dependencies required by your project.

```powershell
./scripts/tests/unreal/setup.ps1 -Engine C:/Unreal/UE_5.x -ReferenceProject A:/Projects/Example/Example.uproject -ReferenceAssets Characters/Example/SK_Example.uasset,Characters/Example/SK_Example_Skeleton.uasset,Characters/Example/Walk.uasset
```

Create a JSON import job with normal importer fields plus:

```json
{
  "name": "BoundTest",
  "glb": "A:/absolute/character.glb",
  "destination": "/Game/Generated/TripoValidation/BoundTest",
  "replace_existing": true,
  "expected_skeletal": true,
  "report": "A:/absolute/animation-validation.json",
  "target": {
    "skeleton": "/Game/Characters/Example/SK_Example_Skeleton",
    "reference_mesh": "/Game/Characters/Example/SK_Example",
    "exact_proportions": false
  },
  "test_animation": "/Game/Characters/Example/Walk"
}
```

Set `TRIPO_RIG_TEST_JOB` to that job's absolute path, then run the isolated project:

```powershell
UnrealEditor-Cmd.exe work/rig_validation/RigValidation.uproject -unattended -nullrhi -run=pythonscript -script=scripts/tests/ue_rig_validate.py
```

The import must match all reference bone names, parents, local axes (within 0.1 degree),
and scales (within 0.001) before it can use the existing skeleton. Setting
`exact_proportions` also checks local translations within 0.05 cm; ordinary reference
characters intentionally retain individual proportions.

The C++ helper plays the supplied AnimSequence on a real
SkeletalMeshComponent, evaluates two animation times, and computes CPU-skinned
vertices from the component's animation transforms and skin weights. It requires
finite output and substantial vertex movement. This proves the animation drives
the mesh, including changed pairwise distances (not just rigid whole-body motion),
but does not prove anatomically correct deformation. Visual review of
the reference pose and walk remains required before calling a character ready.

All imported test assets stay in the isolated project. Source reference assets and
animations are never saved or modified by these scripts.

The report includes the input SHA256 and the actual animation's 37% sampled
world-space bone transforms in centimeters for independent visual review.
