<!-- Copyright (C) 2026 - Joseph M Wilcox -->
# Pipeline boundaries

The product is a command-line job runner producing a self-contained character GLB and a machine-readable character manifest. It does not require an Unreal installation to generate the deliverable.

1. Group named views from an input folder, or read each image independently with --singleimage. The lower-level engine also accepts a supplied rigged GLB/FBX.
2. Generate with Tripo HD/Smart and apply selected texture/topology options.
3. Use Tripo rig-check/rig for an intermediate humanoid when generation needs it.
4. Normalize geometry, materials, units and skinning in headless Blender.
5. Apply the selected output rig profile. Mixamo validates the intermediate. UE5/custom use a weighted reference rig; the reference's bone contract defines the output.
6. Validate the exported GLB and publish `Output/character.glb`.

`scripts/unreal/` is an optional editor import adapter. It is not part of the required generation-to-GLB path. `scripts/tests/unreal/` validates the exported result against a real engine and a supplied animation.

## State and reproducibility

`manifest.json` is operational state. It holds saved task IDs, artifact hashes and stage status so resume never accidentally submits the same paid task twice. Paths inside a build are portable POSIX relative paths; input identity uses content hashes. Credential location, Blender executable path and mount locations do not change recipe identity. Actual processing script contents, reference-rig content, image/source contents and output options do. Changing a recipe or converter requires a new build directory.

The internal `Work/runs/<id>/final/character.json` is the delivery validation contract. It includes a GLB digest, units, measured height, selected rig, actual bone names/hierarchy, material/texture counts and cloud task provenance. It contains no source-machine paths or key material. The final GLB must embed its buffers and textures.

Legacy experimental `tripo.pipeline.v1` builds are retained as history. Start a new job from their downloaded rig GLB to process them with the production converter. Do not edit task IDs or fingerprints by hand to force a different recipe into a paid build.

## Rig profiles

- `source`: preserve and validate the supplied rig, or allow a static mesh.
- `mixamo`: require Mixamo humanoid names and skinning; no claim of UE mannequin compatibility.
- `ue5`/`custom`: explicitly provide the desired weighted reference. Engine version alone does not identify a skeleton. The current humanoid fitter validates its supported joint correspondence and fails on an incompatible reference instead of silently dropping bones.

Tripo rigging policy (`none`, `auto`, `force`) is independent of the output rig profile. The project skeleton selection `rig.spec: "ue5"` resolves during validation to Mixamo biped rigging while preserving the rigging policy and a UE5 output profile using the installation's shared `mixamo2ue5` reference/weights settings. The CLI still requires `--rig`; without it the pass is disabled. Converter assets live in `Converters/Mixamo2UE5`, allowing the project recipe to live in the consuming project. For reference-bound characters, Mixamo is an intermediate, not the delivered skeleton.

## Migration

Invoke `TripoForge <project>.json --generate --input <folder> --output <folder>` or `TripoForge --balance`. Shared config.json plus ignored config.local.json supply tools and credentials. The launcher merges root settings with project overrides while resolving each layer against its own folder. CLI paths resolve against the current directory. Filename discovery produces one recipe per model; --singleimage selects one image per recipe. Each model has Work/<model>/current.json and numbered job folders. Only the current successful job is cleaned unless --messy is supplied; failures and older retained jobs remain. New invocations start separate jobs; --resume reuses the model's latest retained job. Only the final GLB is published, named after the model; --subfolders adds a per-model output directory.

## Linux/Docker

The runner uses standard Python and subprocess argument arrays; Blender is the only local generation tool dependency. Input/config/output/reference rigs and credentials have separate paths suitable for bind mounts. Windows implementation and animation compatibility are the present milestone. Docker image creation and Linux execution have been explicitly deferred; this repository does not claim a tested container release yet.
