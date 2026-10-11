# Copyright (C) 2026 - Joseph M Wilcox
import copy
import json
import re
from pathlib import Path

from .common import PipelineError

DEFAULTS = {
    "name": "Character", "inputs": {}, "source": None,
    "tripo": {"model": "v3.1-20260211", "geometry_quality": "detailed",
              "texture": True, "pbr": True, "export_uv": True, "texture_quality": "detailed",
              "texture_version": "v3.5-20260815", "delight": True,
              "quad": False, "face_limit": 75000},
    "retopology": {"enabled": False, "model": "v2.0", "quad": False,
                   "face_limit": 20000, "bake": True},
    "rig": {"mode": "none", "type": "biped", "spec": "mixamo"},
    "output_rig": {"profile": "source", "reference": None, "weights": "reference"},
    "mixamo2ue5": {"reference": str(Path(__file__).resolve().parents[2] / "Converters" / "Mixamo2UE5" / "reference.blend"),
                   "weights": "reference"},
    "blender": {"executable": "blender", "height_m": 1.8, "yaw_degrees": 0.0},
    "unreal": {"executable": "UnrealEditor-Cmd", "project": None,
               "destination": "/Game/GeneratedCharacters", "replace_existing": False},
    "target": {"skeleton": None, "animation_blueprint": None, "retargeter": None, "reference_mesh": None, "exact_proportions": False},
    "key_env": "TRIPO_API_KEY", "key_file": None, "task_timeout": 3600, "poll_interval": 5,
    "process_timeout": 1800,
}


def merge(base, update):
    result = copy.deepcopy(base)
    for key, value in update.items():
        if key not in result:
            raise PipelineError(f"Unknown configuration key: {key}")
        if isinstance(result[key], dict) and isinstance(value, dict) and key != "inputs":
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def load(path=None, defaults=None):
    result = copy.deepcopy(DEFAULTS if defaults is None else defaults)
    if path:
        path = Path(path).resolve()
        text = path.read_text(encoding="utf-8-sig")
        if path.suffix.lower() in (".yaml", ".yml"):
            try:
                import yaml
            except ImportError:
                raise PipelineError("YAML configs need PyYAML: python -m pip install PyYAML. JSON needs no dependencies.") from None
            data = yaml.safe_load(text)
        else:
            data = json.loads(text)
        if not isinstance(data, dict):
            raise PipelineError("Configuration must be an object.")
        data.pop("_copyright", None)
        result = merge(result, data)
        for view, image in result["inputs"].items():
            result["inputs"][view] = str((path.parent / image).resolve())
        for key in ("source", "key_file"):
            if result[key]:
                result[key] = str((path.parent / result[key]).resolve())
        for stage, field in (("unreal", "project"), ("blender", "executable"), ("unreal", "executable")):
            value = result[stage][field]
            if value and (field == "project" or "/" in value or "\\" in value):
                result[stage][field] = str((path.parent / value).resolve())
        if result["output_rig"]["reference"]:
            result["output_rig"]["reference"] = str((path.parent / result["output_rig"]["reference"]).resolve())
        if result["mixamo2ue5"]["reference"]:
            result["mixamo2ue5"]["reference"] = str((path.parent / result["mixamo2ue5"]["reference"]).resolve())
    return result


def load_settings(path):
    """Load shared settings and an optional ignored sibling machine override."""
    path = Path(path).resolve()
    result = load(path)
    local = path.with_name(path.stem + '.local' + path.suffix)
    if not path.stem.endswith('.local') and local.is_file():
        result = load(local, defaults=result)
    return result


def normalize_rig(cfg):
    """Resolve the requested skeleton spec to the API and converter recipe."""
    # Accept the former selector so recipes created before the spec correction still load.
    if cfg["rig"]["mode"] == "ue5":
        cfg["rig"].update(mode="force", spec="ue5")
    if cfg["rig"]["spec"] == "ue5":
        cfg["rig"]["spec"] = "mixamo"
        if cfg["rig"]["mode"] == "none":
            cfg["output_rig"].update(profile="source", reference=None)
            return cfg
        if cfg["rig"]["type"] != "biped":
            raise PipelineError("UE5 conversion requires a biped rig.")
        cfg["output_rig"].update(profile="ue5", **cfg["mixamo2ue5"])
    return cfg


def validate(cfg):
    normalize_rig(cfg)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", cfg["name"]):
        raise PipelineError("Name must start with a letter and contain only letters, numbers or underscores (max 64).")
    if cfg["source"]:
        source = Path(cfg["source"])
        if not source.is_file() or source.suffix.lower() not in (".glb", ".fbx"):
            raise PipelineError("Source must be an existing GLB or FBX.")
        if cfg["inputs"]:
            raise PipelineError("Choose --source or image inputs, not both.")
    else:
        if set(cfg["inputs"]) not in ({"image"}, {"front", "left", "back", "right"}):
            raise PipelineError("Supply one image, or exactly front, left, back and right images.")
        for view, value in cfg["inputs"].items():
            path = Path(value)
            if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                raise PipelineError(f"{view}: must be an existing PNG/JPEG (Tripo upload endpoint formats).")
            if not 0 < path.stat().st_size <= 20 * 1024 * 1024:
                raise PipelineError(f"{view}: image must be nonempty and <=20 MiB.")
    tripo = cfg["tripo"]
    if tripo["model"] not in ("v3.1-20260211", "v3.0-20250812", "P1-20260311", "P2-20260801"):
        raise PipelineError("Supported models: HD H-series v3.1/v3.0, Smart Mesh P1/P2.")
    for section, keys in ((tripo, ("texture", "pbr", "export_uv", "delight", "quad")),
                          (cfg["retopology"], ("enabled", "quad", "bake")),
                          (cfg["unreal"], ("replace_existing",))):
        for key in keys:
            if type(section[key]) is not bool:
                raise PipelineError(f"{key} must be a boolean.")
    if tripo["geometry_quality"] not in ("standard", "detailed"):
        raise PipelineError("Geometry must be standard or detailed.")
    if tripo["texture_quality"] not in ("fast", "standard", "detailed", "extreme"):
        raise PipelineError("Invalid texture quality.")
    if tripo["texture_version"] not in ("v3.5-20260815", "v3.0-20250812"):
        raise PipelineError("Unsupported texture model.")
    if (tripo["delight"] or tripo["texture_quality"] == "fast") and tripo["texture_version"] != "v3.5-20260815":
        raise PipelineError("De-lighting/fast textures require texture_version v3.5-20260815.")
    if tripo["pbr"] and not tripo["texture"]:
        raise PipelineError("PBR requires textures; disable both for untextured generation.")
    maximum = 150000 if tripo["quad"] else (2000000 if tripo["geometry_quality"] == "detailed" else
                                            1500000 if tripo["model"].startswith("v3.1") else 1000000)
    minimum = 1
    if tripo["model"] == "P1-20260311":
        if tripo["quad"]:
            raise PipelineError("Smart Mesh P1 does not support quads; choose P2.")
        minimum, maximum = 50, 20000
    elif tripo["model"] == "P2-20260801":
        minimum, maximum = 48, 25000 if tripo["quad"] else 50000
    check_faces(tripo["face_limit"], minimum, maximum)
    retopo = cfg["retopology"]
    if retopo["enabled"]:
        if retopo["model"] not in ("v1.0", "v2.0"):
            raise PipelineError("Retopology model must be v1.0 or v2.0.")
        if retopo["model"] == "v2.0":
            check_faces(retopo["face_limit"], 500, 10000 if retopo["quad"] else 20000)
        else:
            check_faces(retopo["face_limit"], 1, 150000 if retopo["quad"] else 2000000)
            if retopo["bake"]:
                raise PipelineError("Basic retopology v1.0 does not support bake; set bake=false.")
    rig = cfg["rig"]
    output_rig = cfg["output_rig"]
    if output_rig["profile"] not in ("source", "mixamo", "ue5", "custom"):
        raise PipelineError("Output rig profile must be source, mixamo, ue5 or custom.")
    if output_rig["weights"] != "reference":
        raise PipelineError("Output rig currently supports reference weights only.")
    if output_rig["profile"] in ("ue5", "custom"):
        reference = output_rig["reference"]
        if not reference or not Path(reference).is_file():
            raise PipelineError("The selected output rig requires an existing --rig-reference file.")
        if Path(reference).suffix.lower() not in (".blend", ".fbx", ".glb"):
            raise PipelineError("Rig reference must be .blend, .fbx or .glb.")
    if rig["mode"] not in ("none", "auto", "force"):
        raise PipelineError("Rig mode must be none, auto or force.")
    if rig["type"] not in ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic"):
        raise PipelineError("Unknown rig type.")
    if rig["spec"] not in ("mixamo", "tripo"):
        raise PipelineError("Rig spec must be mixamo, tripo or ue5.")
    import math
    for value in (cfg["blender"]["height_m"], cfg["task_timeout"], cfg["poll_interval"], cfg["process_timeout"]):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise PipelineError("Height, timeout and poll interval must be finite positive numbers.")
    if not math.isfinite(float(cfg["blender"]["yaw_degrees"])):
        raise PipelineError("Yaw must be finite.")
    if not re.fullmatch(r"/Game(?:/[A-Za-z0-9_]+)+", cfg["unreal"]["destination"]):
        raise PipelineError("Unreal destination must be a /Game/... package path.")


def check_faces(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PipelineError(f"Face limit must be an integer from {minimum} to {maximum} for the selected mode.")
