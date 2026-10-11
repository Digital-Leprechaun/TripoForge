<!-- Copyright (C) 2026 - Joseph M Wilcox -->
# Mixamo2UE5 reference assets

`reference.blend` is a private local prerequisite containing the weighted UE5 Manny Simple reference used by TripoForge's Blender converter. It is excluded from Git and ZIP releases along with exported FBX/GLB assets. `reference.json` records source assets, hashes, bone count and coordinate basis; it is provenance, not the converter's runtime input. Existing local references remain usable.

Build the reference from your own Unreal installation:

1. Enable Unreal Editor's Python scripting plugin and open a project containing your Manny Simple skeletal mesh.
2. Create a local job JSON with `mesh` set to that asset's path (for example `/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple.SKM_Manny_Simple`) and `directory` set to an absolute private export folder. Set `TRIPOFORGE_REFERENCE_JOB` to the job's absolute path before starting the editor.
3. Run `scripts/unreal/export_reference.py` in the editor. It writes `reference.fbx` and `reference.json` containing the mesh and raw bone transform contract.
4. Run Blender with the exported files and a local output path:

```powershell
blender --background --factory-startup --python-exit-code 1 `
  --python scripts/blender/build_reference.py -- `
  --fbx C:/Private/Reference/reference.fbx `
  --contract C:/Private/Reference/reference.json `
  --output C:/Private/Reference/reference.blend
```

5. Set `mixamo2ue5.reference` in ignored `config.local.json` to that `.blend`. Keep raw reference assets private; validate resulting bind transforms and animations in your own target Unreal project. The installed fallback path is `Converters/Mixamo2UE5/reference.blend` if you build it there instead. Regenerated metadata reflects your own source assets.

Projects select `rig.spec: "ue5"`. With `--rig` (or Add rig in the web UI), TripoForge requests a Mixamo biped rig and then fits/binds the generated character to this reference. The API receives `spec: "mixamo"`; UE5 is a local target skeleton specification.

The installation's shared `config.json` has a `mixamo2ue5` section containing `reference` and `weights`. Its reference path resolves relative to the shared settings file. The built-in fallback points to this installation folder. Project JSON files can live elsewhere without copies of these assets. A custom installation reference can be `.blend`, `.fbx`, or `.glb` with a compatible weighted humanoid skeleton. Review resulting deformation and validate animations against the target Unreal project.

The conversion implementation is `scripts/blender/target_rig.py`; Blender executable, height and orientation settings still apply to general model processing.
