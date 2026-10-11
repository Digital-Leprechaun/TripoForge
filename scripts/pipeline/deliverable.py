# Copyright (C) 2026 - Joseph M Wilcox
"""Validated, self-contained GLB + manifest output contract (no Unreal dependency)."""
import hashlib
import json
import shutil
import struct
from pathlib import Path

from .common import PipelineError, sha256, write_json
from . import __version__


def inspect_glb(path):
    path = Path(path)
    with path.open("rb") as stream:
        header = stream.read(12)
        if len(header) != 12 or struct.unpack("<4sII", header) != (b"glTF", 2, path.stat().st_size):
            raise PipelineError("Final GLB has invalid header, version, or length.")
        length, kind = struct.unpack("<II", stream.read(8))
        if kind != 0x4E4F534A or length > path.stat().st_size - 20:
            raise PipelineError("Final GLB has no valid JSON chunk.")
        doc = json.loads(stream.read(length))
    if doc.get("asset", {}).get("version") != "2.0":
        raise PipelineError("Final artifact must be glTF 2.0.")
    if any("uri" in b for b in doc.get("buffers", [])):
        raise PipelineError("Final GLB contains an external buffer; self-contained output is required.")
    if any("bufferView" not in image for image in doc.get("images", [])):
        raise PipelineError("Final GLB contains a non-embedded image.")
    meshes = doc.get("meshes", [])
    if not meshes or not any(m.get("primitives") for m in meshes):
        raise PipelineError("Final GLB has no mesh primitives.")
    nodes = doc.get("nodes", [])
    skins = []
    for skin in doc.get("skins", []):
        joints = skin.get("joints", [])
        if not joints or any(type(j) is not int or j < 0 or j >= len(nodes) for j in joints):
            raise PipelineError("Final GLB skin has invalid joint indices.")
        names = [nodes[j].get("name", "") for j in joints]
        if any(not name for name in names) or len(set(names)) != len(names):
            raise PipelineError("Final skeleton has empty or duplicate bone names.")
        parents = {child: parent for parent, node in enumerate(nodes) for child in node.get("children", [])}
        contract = [{"name": nodes[j]["name"], "parent": nodes[parents[j]].get("name") if j in parents else None,
                     **{k: nodes[j][k] for k in ("translation", "rotation", "scale", "matrix") if k in nodes[j]}}
                    for j in joints]
        skins.append({"bones": names, "hierarchy": contract,
                      "hierarchy_sha256": hashlib.sha256(json.dumps(contract, sort_keys=True).encode()).hexdigest()})
    for node in nodes:
        if "skin" in node:
            if "mesh" not in node or not 0 <= node["skin"] < len(skins):
                raise PipelineError("Skinned node lacks a mesh or valid skin.")
            for primitive in meshes[node["mesh"]]["primitives"]:
                attrs = primitive.get("attributes", {})
                if not {"JOINTS_0", "WEIGHTS_0"} <= attrs.keys():
                    raise PipelineError("Skinned mesh primitive lacks joint/weight attributes.")
    return {"meshes": len(meshes), "materials": len(doc.get("materials", [])),
            "textures": len(doc.get("images", [])), "skins": skins}


def package(source, folder, name, profile, report, provenance):
    details = inspect_glb(source)
    if profile != "source" and not details["skins"]:
        raise PipelineError("Selected rig profile requires a skinned final GLB.")
    if profile == "mixamo":
        names = set(details["skins"][0]["bones"])
        required = {"mixamorig:Hips", "mixamorig:Spine", "mixamorig:Head", "mixamorig:LeftArm", "mixamorig:RightArm"}
        if not required <= names:
            raise PipelineError("Mixamo profile requested but required Mixamo joints are missing.")
    if profile in ("ue5", "custom"):
        expected = report.get("bones")
        if not isinstance(expected, dict) or not expected:
            raise PipelineError("Target rig report has no reference bone contract.")
        for skin in details["skins"]:
            if set(skin["bones"]) != set(expected):
                raise PipelineError("Final exported skeleton differs from the target rig report.")
            for bone in skin["hierarchy"]:
                parent = bone["parent"] if bone["parent"] in expected else None
                if expected[bone["name"]] != parent:
                    raise PipelineError("Final exported bone hierarchy differs from target reference.")
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    glb = folder / "character.glb"
    temporary = folder / "character.glb.tmp"
    shutil.copyfile(source, temporary)
    temporary.replace(glb)
    result = {"schema": "tripo.character.v1", "pipeline_version": __version__, "name": name, "file": "character.glb", "sha256": sha256(glb),
              "format": "glTF 2.0 binary", "units": "meters", "up": "+Y", "height_m": report.get("height_m"),
              "rig_profile": profile, "rigged": bool(details["skins"]), "geometry": details,
              "validation": {"self_contained": True, "blender_success": report.get("success", False)},
              "provenance": provenance}
    if profile in ("ue5", "custom"):
        result["rig_conversion"] = {key: report[key] for key in ("weight_transfer", "weights_preserved", "uvs_preserved",
                                     "animation_compatibility", "warnings") if key in report}
    write_json(folder / "character.json", result)
    return glb, result
