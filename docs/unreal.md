<!-- Copyright (C) 2026 - Joseph M Wilcox -->
# Unreal editor import

`scripts/unreal/import_character.py` runs synchronously inside UnrealEditor-Cmd. The normal pipeline invokes it with `--until unreal --target path/to/target.json --project <uproject> --unreal <UnrealEditor-Cmd.exe>`. The default pipeline stops at the final GLB.

For a standalone import, set `TRIPO_IMPORT_JOB` to an absolute JSON file:

```json
{
  "glb": "A:/Assets/character.glb",
  "name": "MyCharacter",
  "destination": "/Game/Generated/Tripo/MyCharacter",
  "report": "A:/Assets/import-report.json",
  "replace_existing": false,
  "expected_skeletal": true,
  "target": {
    "skeleton": "/Game/Characters/Example/SK_Example_Skeleton",
    "reference_mesh": "/Game/Characters/Example/SK_Example",
    "exact_proportions": false
  }
}
```

Run:

```powershell
$env:TRIPO_IMPORT_JOB = 'A:/Assets/import-job.json'
& 'C:/Unreal/UE_5.x/Engine/Binaries/Win64/UnrealEditor-Cmd.exe' `
  'A:/Projects/MyGame/MyGame.uproject' -run=pythonscript `
  '-script=C:/Tools/TripoForge/scripts/unreal/import_character.py' `
  -unattended -nosplash -NullRHI `
  -EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,InterchangeEditor,SkeletalMeshModelingTools
```

Existing destination assets are refused unless replacement is explicit. Replacement is not transactional; a failed final import may leave partial assets. Use a dedicated destination and inspect the report before retrying. The importer reports assets, material references, skeleton references and bounds. An optional `expected_height_cm` checks actual scale.

## Binding an existing skeleton

With a target skeleton/reference mesh, the importer first imports into a unique disposable staging folder and checks actual bones, parents, local rotations/scales and child translation directions. Individual character bone lengths may vary. `exact_proportions: true` additionally checks local translation magnitudes. Helper offsets may vary, but their names, hierarchy and axes must match.

Only after this check does the final Interchange import use the existing skeleton. Reference pose updates and curve metadata additions are disabled. The final mesh is checked again. This does not retarget a mismatched Mixamo model; choose the appropriate output rig during generation. Animation Blueprint and retargeter references are checked when supplied; gameplay setup remains separate.

Supply the desired skeleton and weighted reference mesh in a target JSON file under the owning project’s Source folder. The lower-level engine CLI accepts its explicit path with --target.

## Validation

The Windows UE5.8.2 isolated test project validated the final Malcolm GLB against the actual shared skeleton: 70 bones, maximum local rotation error about 0.000673 degrees. The real shared Walk_Forward animation deformed 28,562 vertices with a maximum displacement of about 48.06 cm. The target skeleton file hash was unchanged after the test. See `scripts/tests/unreal/README.md` for the reproducible harness.

Both the fresh JSON report and engine exit status matter. The runner reports successful assets plus an unrelated nonzero engine exit as `complete_with_warnings` and exit code 2. Complete success is 0; pipeline failure is 1. Do not reuse a stale report as success.

Editor import tests do not certify a packaged Shipping game, physics, collision or weapon grip.
