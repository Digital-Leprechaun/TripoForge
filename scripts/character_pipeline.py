# Copyright (C) 2026 - Joseph M Wilcox
"""Command-line entry point; JSON configs work with Python standard library only."""
import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

from pipeline import config
from pipeline.common import PipelineError, read_json, write_json
from pipeline.runner import Build, ROOT


def parser():
    result = argparse.ArgumentParser(description="Four views -> Tripo -> Blender -> GLB -> UE5")
    commands = result.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--config")
    build.add_argument("--name")
    for view in ("front", "left", "back", "right"):
        build.add_argument("--" + view)
    build.add_argument("--source", help="Existing GLB/FBX; skips generation")
    build.add_argument("--mesh-mode", choices=("hd", "smart"), help="HD H3.1 or Smart Mesh P2")
    build.add_argument("--model", choices=("v3.1-20260211", "v3.0-20250812", "P1-20260311", "P2-20260801"))
    build.add_argument("--geometry", choices=("standard", "detailed"))
    build.add_argument("--texture", choices=("fast", "standard", "detailed", "extreme"))
    build.add_argument("--delight", action=argparse.BooleanOptionalAction, default=None)
    build.add_argument("--topology", choices=("triangle", "quad"))
    build.add_argument("--faces", type=int)
    build.add_argument("--retopo-faces", type=int)
    build.add_argument("--retopo-model", choices=("v1.0", "v2.0"))
    build.add_argument("--retopo-topology", choices=("triangle", "quad"))
    build.add_argument("--rig", choices=("none", "auto", "force", "ue5"))
    build.add_argument("--rig-type")
    build.add_argument("--rig-profile", choices=("source", "mixamo", "ue5", "custom"), help="Final skeleton contract; independent of Tripo rigging")
    build.add_argument("--rig-reference", help="Canonical rigged .blend/FBX/GLB for UE5 or custom profile")
    build.add_argument("--rig-weights", choices=("reference",))
    build.add_argument("--blender")
    build.add_argument("--height-m", type=float)
    build.add_argument("--yaw-degrees", type=float)
    build.add_argument("--unreal")
    build.add_argument("--project")
    build.add_argument("--destination")
    build.add_argument("--target", help="Path to a project-owned target JSON file")
    build.add_argument("--replace-existing", action="store_true", default=None)
    build.add_argument("--key-file", help="Local plaintext key file; contents never saved to manifest")
    build.add_argument("--key-env")
    build.add_argument("--output", help="Work directory; prefer the TripoForge project launcher")
    build.add_argument("--until", choices=("tripo", "blender", "unreal"), default="blender")
    build.add_argument("--resume", action="store_true")
    build.add_argument("--dry-run", action="store_true", help="Validate and show config without uploads/processes")
    doctor = commands.add_parser("doctor")
    doctor.add_argument("--config")
    verify = commands.add_parser("validate", help="Inspect self-contained GLB geometry and rig without Blender or network")
    verify.add_argument("glb")
    adopt = commands.add_parser("adopt-task", help="Recover a submission whose response was lost")
    adopt.add_argument("--output", required=True)
    adopt.add_argument("--stage", required=True, choices=("generation", "retopology", "rig_check", "rig"))
    adopt.add_argument("--task-id", required=True)
    return result


def configure(args):
    cfg = config.load(args.config)
    if args.target:
        target_path = Path(args.target)
        target = read_json(target_path)
        if target.get("project"):
            cfg["unreal"]["project"] = target["project"]
        if target.get("destination_root"):
            cfg["unreal"]["destination"] = target["destination_root"]
        cfg["target"] = {key: target.get(key, cfg["target"][key]) for key in cfg["target"]}
    mapping = {"name": (None, "name"), "source": (None, "source"), "model": ("tripo", "model"),
               "geometry": ("tripo", "geometry_quality"), "texture": ("tripo", "texture_quality"),
               "delight": ("tripo", "delight"), "faces": ("tripo", "face_limit"), "rig": ("rig", "mode"),
               "rig_type": ("rig", "type"), "blender": ("blender", "executable"), "height_m": ("blender", "height_m"),
               "yaw_degrees": ("blender", "yaw_degrees"), "unreal": ("unreal", "executable"),
               "project": ("unreal", "project"), "destination": ("unreal", "destination"),
               "replace_existing": ("unreal", "replace_existing"), "key_file": (None, "key_file"), "key_env": (None, "key_env")}
    if args.mesh_mode:
        cfg["tripo"]["model"] = "P2-20260801" if args.mesh_mode == "smart" else "v3.1-20260211"
        if args.faces is None:
            cfg["tripo"]["face_limit"] = 20000 if args.mesh_mode == "smart" else 75000
    for argument, (section, key) in mapping.items():
        value = getattr(args, argument)
        if value is not None:
            (cfg[section] if section else cfg)[key] = value
    for view in ("front", "left", "back", "right"):
        if getattr(args, view):
            cfg["inputs"][view] = str(Path(getattr(args, view)).resolve())
    if args.topology:
        cfg["tripo"]["quad"] = args.topology == "quad"
    if args.retopo_faces is not None:
        cfg["retopology"].update(enabled=True, face_limit=args.retopo_faces)
    if args.retopo_model:
        cfg["retopology"]["model"] = args.retopo_model
        if args.retopo_model == "v1.0":
            cfg["retopology"]["bake"] = False
    if args.retopo_topology:
        cfg["retopology"]["quad"] = args.retopo_topology == "quad"
    if args.rig_profile:
        cfg["output_rig"]["profile"] = args.rig_profile
    if args.rig_reference:
        cfg["output_rig"]["reference"] = str(Path(args.rig_reference).resolve())
    if args.rig_weights:
        cfg["output_rig"]["weights"] = args.rig_weights
    if cfg["output_rig"]["profile"] != "source" and not cfg["source"] and args.rig is None:
        cfg["rig"].update(mode="force", type="biped", spec="mixamo")
    for key in ("source", "key_file"):
        if cfg[key]:
            cfg[key] = str(Path(cfg[key]).resolve())
    config.validate(cfg)
    return cfg


def main():
    args = parser().parse_args()
    try:
        if args.command == "validate":
            from pipeline.deliverable import inspect_glb
            print(json.dumps(inspect_glb(args.glb), indent=2))
            return 0
        if args.command == "doctor":
            cfg = config.load(args.config)
            print(json.dumps({"python": sys.version.split()[0],
                              "blender": shutil.which(cfg["blender"]["executable"]),
                              "unreal": shutil.which(cfg["unreal"]["executable"]),
                              "project_exists": bool(cfg["unreal"]["project"] and Path(cfg["unreal"]["project"]).is_file()),
                              "key_configured": bool(os.environ.get(cfg["key_env"]) or (cfg["key_file"] and Path(cfg["key_file"]).is_file()))}, indent=2))
            return 0
        if args.command == "adopt-task":
            path = Path(args.output) / "manifest.json"
            if (path.parent / ".running").exists():
                raise PipelineError("Cannot adopt a task while build is running.")
            data = read_json(path)
            entry = data["stages"].get(args.stage, {})
            if entry.get("status") != "submitting" or entry.get("task_id"):
                raise PipelineError("Only an uncertain submission without task_id can be adopted.")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", args.task_id):
                raise PipelineError("Invalid task ID.")
            entry.update(task_id=args.task_id, status="queued")
            write_json(path, data)
            print("Task adopted. Resume with the original build options.")
            return 0
        cfg = configure(args)
        if args.dry_run:
            print(json.dumps({"config": cfg, "until": args.until, "network_requests": 0}, indent=2))
            return 0
        output = args.output or str(ROOT / "Work" / cfg["name"] / "engine")
        manifest = Build(cfg, output, resume=args.resume).run(args.until)
        print("Completed: " + str(manifest))
        if read_json(manifest).get("status") == "complete_with_warnings":
            print("WARNING: Assets imported, but Unreal reported engine errors. See build logs.", file=sys.stderr)
            return 2
        return 0
    except (PipelineError, OSError, ValueError, KeyError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
