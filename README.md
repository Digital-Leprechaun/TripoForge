<!-- Copyright (C) 2026 - Joseph M Wilcox -->
# TripoForge

Copyright (C) 2026 - Joseph M Wilcox. Licensed under the [MIT License](LICENSE).

Bulk-generate GLB models from groups of images, or one model per image. A project JSON file supplies generation settings; command flags select the input and output folders.

```powershell
TripoForge ExampleProjectFiles/MixamoExample/project.json --generate --input Images --output Models
TripoForge ExampleProjectFiles/UE5Example/project.json --generate --input Images --output Models --rig --subfolders
TripoForge ExampleProjectFiles/MixamoExample/project.json --generate --input Images --output Models --singleimage
TripoForge --balance
TripoForge --web
TripoForge --help
```

Project JSON, input and output command-line paths are relative to your current directory; absolute paths also work. Shared settings default to the installation's config.json. From an installation not on PATH, use `.\TripoForge.ps1` with the same arguments.

No arguments, `--h`, `-h`, and `--help` show help. `--generate`, `--balance`, and `--web` are mutually exclusive command flags. Generation is the default when a project JSON file and input folder are supplied. `--output` is optional: `TripoForge project.json --generate --input Images` writes GLBs to `Images/output`.

## Web interface

Run `TripoForge --web` to open the local controller at `http://127.0.0.1:8768`. Keep its terminal running; Ctrl+C stops the server. Python's standard library is sufficient.

Use `TripoForge <project>.json --web` to start with a particular project, or add `--settings <json>` to use another shared settings file. Generation options are selected inside the browser. Add rig follows the project's `rig.spec`, including `ue5`. New web tasks default to Quads; P1 falls back to Tris because it does not support quads. The face limit is clamped to the selected model/topology's range.

PBR and No Lighting checkboxes initially inherit the project's PBR and de-lighting values and can override them per task. No Lighting removes baked-in lighting from reference images and requires texture version `v3.5-20260815`; the checkbox is unavailable with older texture versions. No texture / UVs disables both material controls. Saved queue cards show these selections.

SmartUVs and Upscale textures are shown as unavailable because a public v3 option for those specific Studio operations has not been verified. [Smart UV](https://www.tripo3d.ai/blog/smart-uv) is a separate Studio operation; ordinary `export_uv` unwrapping remains enabled for textured tasks. The API's [extreme texture quality](https://developers.tripo3d.ai/en/docs/models-texture) generates 8K textures, which is distinct from upscaling existing textures. No extra UV/upscale stage or credit charge is added by the unavailable checkboxes.

Load a project JSON with the path field or Find button. Drop a PNG/JPEG onto each Front, Left, Right and Back slot (clicking a slot also opens an image picker). Choose Tris/Quads, set the face limit with the slider or number field, and name the model. Optional controls enable rigging, omit textures/UVs, use model subfolders, or retain intermediates.

Checking Add rig reveals Spec and Type dropdowns. Spec offers Mixamo, Tripo, and UE5 (our shared converter). Type offers biped, quadruped, hexapod, octopod, avian, serpentine and aquatic; UE5 requires biped. The dropdowns initially use the project settings and override the rig target per queued task. Queue cards show the saved spec/type.

Add task saves a copy of the images and settings in `WebWork`, so queued tasks survive a restart and later editor changes. The queue offers Remove, Estimate cost, and Run all tasks. Runs process the current pending tasks sequentially and show status and errors. Failed tasks can be resumed using saved task IDs; interrupted process locks require manual verification/removal as in the CLI. Cost estimates use the same pricing as `--cost`; adding or costing tasks does not generate models. The top banner refreshes credits and links to Tripo's platform to purchase more.

Browser uploads hide their original PC directory. For web tasks, an empty output field therefore defaults to `<project directory>/output`; a custom path overrides it. The generation pipeline creates the destination when publishing the GLB. CLI output defaults remain `<input>/output`.

The controller binds only to localhost. API keys stay on the Python side. Uploads are limited to four PNG/JPEG files of at most 20 MiB each. `WebWork` is excluded from distribution packages.

## Command options

| Flag | Behavior |
|---|---|
| `--generate` | Generate models; also the default when project JSON and input folder are supplied. |
| `--balance` | Read available and frozen API credits, optionally using project overrides. |
| `--web` | Open the localhost web controller; accepts an optional project JSON and `--settings`. |
| `-h` / `--h` / `--help` | Show help; running with no arguments also shows help. |
| `--input <folder>` | Folder containing PNG/JPEG images; reads files directly in that folder. |
| `--output <folder>` | Destination for final GLBs, named `<model>.glb`. Defaults to `<input>/output` when omitted. |
| `--subfolders` | Publish each model as `<output>/<model>/<model>.glb`. |
| `--dry-run` | Display detected groups, inputs, output paths and processing options without API requests, builds or file creation. |
| `--cost` | Estimate a full new run in credits and USD without API requests or building. Output folder is optional. |
| `--messy` | Keep intermediate models, logs, reports and manifests between runs. |
| `--notexture` | Omit textures, PBR maps and UV unwrapping; disable texture baking during retopology. |
| `--pbr` / `--no-pbr` | Enable or disable PBR maps; when omitted, use the JSON setting. `--pbr` requires textures. |
| `--delight` / `--no-delight` | Enable or disable texture de-lighting; when omitted, use the JSON setting. |
| `--rig` | Force a rigging attempt using the project's rig type and `rig.spec`. `spec: ue5` runs Mixamo rigging followed by our shared converter. Without this flag, rigging is off. |
| `--topology triangle\|quad` | Override the project JSON topology for each model. |
| `--faces <count>` / `--face-limit <count>` | Override the generation polygon limit. Model-specific limits are checked before generation. |
| `--singleimage` / `-singleimage` | Use Tripo's single-image endpoint, creating one model for each input image. |
| `--resume` | Continue the latest retained job for each model, with the same inputs and recipe. |
| `--settings <json>` | Override the installation's shared settings file. |

## Filename groups

Use a shared model name plus a view label. For example, these twelve files make three models:

```text
robot_front.png   robot_left.png   robot_back.png   robot_right.png
crate_front.png   crate_left.png   crate_back.png   crate_right.png
alien_front.png   alien_left.png   alien_back.png   alien_right.png
```

Outputs are `robot.glb`, `crate.glb` and `alien.glb`. View labels are case-insensitive; underscores, hyphens, spaces and dots are accepted separators. Prefix labels such as `front_robot.png` also work. Files named only `front.png`, `left.png`, `back.png` and `right.png` form one model using the project's `name`.

Each multiview group must contain all four views. Duplicate views, missing views, unlabelled images and ambiguous labels stop the batch before any generation begins. Groups are identified by names, never by arbitrary file order or every four files. Non-image files are ignored. In single-image mode every image is independent and its filename stem becomes the output model name; duplicate stems are rejected.

Topology and face count use project JSON defaults unless overridden:

```powershell
TripoForge project.json --generate --input Images --topology triangle --faces 10000
TripoForge project.json --input Images --topology quad --faces 5000 --cost
```

These overrides also apply to dry-run previews and cost estimates. They control generation; an explicitly configured retopology stage still has its own settings. P2 permits 48–50,000 triangle faces or 48–25,000 quad faces; P1 supports only triangles, from 50–20,000. Face limit is a maximum rather than an exact-count guarantee. Quad geometry is triangulated when exported to the final GLB.

## Example project files

```text
TripoForge/
  TripoForge.ps1
  TripoForge.cmd
  config.json
  scripts/
  Converters/
    Mixamo2UE5/
      reference.blend     # shared weighted UE5 Manny reference
      reference.json      # source provenance
  ExampleProjectFiles/               # example recipes; your recipe can live in your own project
    UE5Example/
      project.json
    MixamoExample/
      project.json
```

Each example folder contains only `project.json`. Copy the desired recipe into your consuming project and supply your own images through `--input`. Keep matching front/left/back/right views for each model. Choose MixamoExample for Mixamo rigs or UE5Example for the local UE5 converter; add `--rig` to enable either rigging pass.

`project.json` owns model, quality, topology, height and rig selection settings and can live with the consuming project. Folder discovery replaces its `inputs` and `source` for each generated model; filenames determine model names. Defaults enable textures and UVs but skip rigging. PBR and de-lighting use the JSON values unless overridden with `--pbr`/`--no-pbr` or `--delight`/`--no-delight`. `--notexture` omits textures, PBR and UVs; it cannot be combined with `--pbr`.

`--rig` enables rigging. A project with `"rig": {"mode": "force", "spec": "ue5"}` requests the shared Mixamo2UE5 conversion: internally the cloud rig uses `mode: force`, `type: biped`, `spec: mixamo`, followed by the local Blender converter. The project no longer needs an `output_rig` section or a local copy of the reference. Without `--rig`, the mode remains disabled. Other project rig modes use forced rigging when `--rig` is supplied. Explicit `output_rig` settings remain supported for existing/custom workflows.

The installation's shared settings own the `mixamo2ue5` section (`reference`, `weights`); its relative reference path resolves against that settings file. Epic's mannequin reference is a local prerequisite and is excluded from Git and release archives. Build it from your own Unreal installation using [the reference setup instructions](Converters/Mixamo2UE5/README.md). `reference.json` records provenance for the 89-bone Manny Simple reference. Supported humanoid correspondence and animation compatibility must be verified for each reference. General `blender` settings remain available to all models, including static meshes.

## Shared settings

Settings load in this order, with later values overriding earlier ones:

1. Built-in defaults in `scripts/pipeline/config.py`.
2. TripoForge's shared `config.json`, or the file selected by `--settings`.
3. Its optional ignored sibling `config.local.json` (or `<settings-name>.local.json`).
4. The project JSON supplied on the command line.
5. CLI options.

Omitted entries inherit earlier values. The CLI always enables textures and UVs unless `--notexture` is supplied, and disables rigging unless `--rig` is supplied. `--rig` selects the forced policy regardless of the project's `rig.mode`; `rig.spec` still selects the skeleton. PBR and de-lighting inherit the JSON values unless their CLI overrides are supplied.

Keep machine-specific settings in the ignored `config.local.json`. For example:

```json
{
  "key_env": "TRIPO_API_KEY",
  "key_file": "C:/Private/Tripo3DKey.txt",
  "mixamo2ue5": {
    "reference": "Converters/Mixamo2UE5/reference.blend",
    "weights": "reference"
  },
  "blender": {
    "executable": "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"
  },
  "unreal": {
    "executable": "UnrealEditor-Cmd"
  },
  "task_timeout": 3600,
  "poll_interval": 5,
  "process_timeout": 1800
}
```

Keep the generation recipe with the project consuming the models. This portable project JSON requests 7,000 quads and selects our UE5 conversion:

```json
{
  "tripo": {
    "quad": true,
    "model": "P2-20260801",
    "face_limit": 7000,
    "texture": true,
    "pbr": true,
    "texture_quality": "detailed",
    "delight": true
  },
  "rig": {
    "mode": "force",
    "type": "biped",
    "spec": "ue5"
  },
  "blender": {
    "height_m": 1.8
  }
}
```

No `output_rig` section or project-local reference copy is needed for `spec: ue5`. TripoForge translates that specification to `mixamo` for the API and binds the result using the shared reference. It does not request a native UE5 rig from Tripo's public API. A `name` entry is optional and supplies the model name when input files are named only by view. CLI folder discovery supplies the images, so this recipe needs no `inputs` section.

```powershell
TripoForge A:/MyGame/project.json --generate --input A:/MyGame/Images --rig --pbr --delight
TripoForge A:/MyGame/project.json --input A:/MyGame/Images --rig --no-pbr --no-delight --cost
```

Without overrides, built-in generation defaults are H3.1 (`v3.1-20260211`), detailed geometry, 75,000 triangles, detailed textures (`v3.5-20260815`), PBR, UVs and de-lighting enabled, and no rigging or retopology. Blender height defaults to 1.8 metres with 0° yaw. The MixamoExample recipe overrides the generation model to P2 and its face limit to 20,000.

`TripoForge --balance` displays available credits and frozen credits reserved for in-progress tasks. It uses the shared settings and credentials, requires no project or Blender, and does not submit generation jobs. Use `TripoForge --balance --settings <path>` for another shared config, or `TripoForge <project>.json --balance` to include that project's credential overrides.

Public `config.json` sets `key_file` to null and uses `TRIPO_API_KEY`. An ignored `config.local.json` can specify an external key file and installed tool paths; key contents are never copied into the project or output. A configured key file overrides the environment variable. Set `key_file` to null in your local settings to use the environment variable instead. `_copyright` is optional file metadata and does not affect the recipe.

Only run project JSON and local settings from sources you trust: executable settings can point at arbitrary programs which the pipeline will run.

Paths in a shared settings file resolve relative to that file; paths in project.json resolve relative to that project's folder. CLI paths resolve relative to the current working directory. The built-in converter-reference fallback points to this TripoForge installation's `Converters/Mixamo2UE5/reference.blend`. Blender runs headlessly. Python 3.11+ and Blender are required for generation; JSON needs no pip dependencies.

## Work and output

Successful builds publish only the final GLB in the selected output folder, creating that folder when needed. The GLB embeds textures and skinning when enabled. Each new generation run uses API credits; `--cost` and `--dry-run` do not. CLI models run sequentially; a failure stops the batch and retains its work. The web queue records a failed task and continues with subsequent tasks.

```powershell
TripoForge project.json --generate --input Images --output Models --messy
TripoForge project.json --generate --input Images --output Models --resume --messy
TripoForge project.json --generate --input Images --output Models --dry-run
```

`--messy` keeps downloaded models, Blender files, logs, reports and job manifests in `<project folder>/Work/<model>/runs/<job>`. Failed jobs also retain work so `--resume` can continue without duplicating known paid tasks. Default cleanup removes only the current successful job, preserving older failed or messy jobs. A cleaned successful job cannot be resumed. Changed inputs or options require a new run without `--resume`. The previous final GLB is replaced only after a new final artifact passes validation.

Each model's `current.json` identifies its latest attempted job. A project lock prevents overlapping builds using the same project JSON folder. Output must be outside Work so cleanup cannot remove delivered GLBs.

## Cost estimates

```powershell
TripoForge project.json --cost --input Images
TripoForge project.json --generate --input Images --output Models --cost --dry-run --rig
```

`--cost` always stops at the estimate, even with `--generate`. It shows each model's generation, texture, detailed geometry, quad, retopology and rigging costs, plus the batch total. It honors the same settings and overrides as generation, including `--singleimage` and `--notexture`. Combine it with `--dry-run` to also see inputs and output paths. No API key or Blender executable is needed for estimating.

Rates are a local snapshot of [Tripo's published API pricing](https://developers.tripo3d.ai/en/pricing), checked 2026-10-06, using $0.01 USD per credit. P1/P2 have separate base rates; P-series ignores geometry_quality. The estimator can show an automatic-rigging range for lower-level `auto` recipes; the normal CLI's `--rig` selects forced rigging. Local Blender processing, including Mixamo2UE5 conversion, has no Tripo credit cost. Estimates cover a full new run, cannot be combined with --resume, and are not a billing guarantee; actual task credits_consumed is authoritative. Update scripts/pipeline/pricing.py when Tripo changes its published rates.

## Development

All Python code is under scripts. `scripts/character_pipeline.py` is the lower-level engine CLI; routine usage goes through TripoForge. Unreal editor import remains an optional separate consumer; see docs/unreal.md. No runtime loader is included.

```powershell
python -m unittest discover -s scripts/tests -p "test_*.py"
```

Windows is the current supported workflow. Linux/Docker packaging is deferred. Previous experiments are preserved outside the installation and are not included in the current source package.
