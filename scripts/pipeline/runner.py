# Copyright (C) 2026 - Joseph M Wilcox
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .common import PipelineError, read_json, sha256, write_json
from .tripo import TripoClient, RejectedRequest
from .deliverable import package

ROOT = Path(__file__).resolve().parents[2]


def executable(value):
    found = shutil.which(value)
    if not found:
        raise PipelineError(f"Executable not found: {value}")
    return str(Path(found).resolve())


class Build:
    def __init__(self, config, directory, resume=False, client=None):
        self.config = config
        self.directory = Path(directory).resolve()
        self.path = self.directory / "manifest.json"
        inputs = ({"source": config["source"]} if config["source"] else config["inputs"])
        if config["source"]:
            sidecars = Path(config["source"]).with_suffix(".fbm")
            if sidecars.is_dir():
                inputs.update({"fbm/" + p.relative_to(sidecars).as_posix(): str(p) for p in sidecars.rglob("*") if p.is_file()})
        self.input_hashes = {k: {"path": str(Path(v).resolve()), "sha256": sha256(v)} for k, v in inputs.items()}
        # Runtime locations and credentials do not define artifact identity. Input/reference
        # content and generation/conversion options do: mounts can change across hosts.
        semantic = {k: config[k] for k in ("name", "tripo", "retopology", "rig")}
        semantic["blender"] = {k: config["blender"][k] for k in ("height_m", "yaw_degrees")}
        semantic["output_rig"] = dict(config["output_rig"])
        reference = semantic["output_rig"].pop("reference")
        semantic["output_rig"]["reference_sha256"] = sha256(reference) if reference else None
        semantic["processing_scripts"] = {"normalize": sha256(ROOT / "scripts" / "blender" / "process.py")}
        if config["output_rig"]["profile"] in ("ue5", "custom"):
            semantic["processing_scripts"]["target_rig"] = sha256(ROOT / "scripts" / "blender" / "target_rig.py")
        fingerprint = hashlib.sha256(json.dumps({"config": semantic, "inputs": {k: v["sha256"] for k,v in self.input_hashes.items()}}, sort_keys=True).encode()).hexdigest()
        if self.path.exists():
            if not resume:
                raise PipelineError("Build exists. Use --resume with identical options, or a new --output directory.")
            self.manifest = read_json(self.path)
            if self.manifest.get("fingerprint") != fingerprint:
                raise PipelineError("Recipe, processing code or input content changed. Choose a new build directory.")
        else:
            if self.directory.exists() and any(self.directory.iterdir()):
                raise PipelineError("Output directory is nonempty and has no build manifest.")
            self.manifest = {"schema": "tripo.pipeline.v2", "name": config["name"], "fingerprint": fingerprint,
                             "config": config, "inputs": self.input_hashes, "stages": {}, "status": "created"}
        self.client = client

    def save(self):
        write_json(self.path, self.manifest)

    def api(self):
        if self.client is None:
            self.client = TripoClient.from_config(self.config)
        return self.client

    def artifact(self, entry):
        value = entry.get("artifact")
        if not value:
            return None
        path = (self.directory / value["path"]).resolve()
        if not path.is_relative_to(self.directory):
            raise PipelineError("Manifest artifact escapes the build directory.")
        if not path.is_file() or sha256(path) != value["sha256"]:
            raise PipelineError(f"Build artifact missing or changed: {path}")
        for sidecar in entry.get("sidecars", []):
            companion = (self.directory / sidecar["path"]).resolve()
            if not companion.is_relative_to(self.directory) or not companion.is_file() or sha256(companion) != sidecar["sha256"]:
                raise PipelineError("FBX companion texture missing or changed in build directory.")
        return path

    def record_artifact(self, entry, path):
        entry["artifact"] = {"path": Path(path).relative_to(self.directory).as_posix(), "sha256": sha256(path)}
        self.save()

    def remote(self, name, endpoint, payload, model=True):
        stages = self.manifest["stages"]
        entry = stages.setdefault(name, {"endpoint": endpoint, "payload": payload})
        if entry.get("endpoint") != endpoint or entry.get("payload") != payload:
            raise PipelineError(f"Stage {name} input changed; use a new build.")
        cached = self.artifact(entry)
        if cached:
            return cached
        if not model and entry.get("status") == "success":
            return entry["output"]
        if not entry.get("task_id"):
            if entry.get("status") == "submitting":
                raise PipelineError(f"Stage {name} has an uncertain submission. Check Tripo dashboard, "
                                    f"then use adopt-task --stage {name} --task-id ID; no duplicate task was submitted.")
            entry["status"] = "submitting"
            self.save()
            try:
                task_id = self.api().submit(endpoint, payload)
            except RejectedRequest:
                entry["status"] = "rejected"
                self.save()
                raise
            entry.update(task_id=task_id, status="queued")
            self.save()
        print(f"{name}: polling {entry['task_id']}", flush=True)
        def progress(data):
            entry.update(data)
            self.save()
        task = self.api().wait(entry["task_id"], progress)
        # Don't persist expiring signed output URLs. Keep task IDs and non-URL metadata.
        entry["status"] = "success"
        entry["credits_consumed"] = task.get("credits_consumed")
        if model:
            path = self.api().download_model(task, self.directory / name)
            self.record_artifact(entry, path)
            return path
        entry["output"] = task.get("output", {})
        self.save()
        return entry["output"]

    def upload(self, name, path):
        uploads = self.manifest.setdefault("uploads", {})
        digest = sha256(path)
        entry = uploads.get(name)
        if entry and entry["sha256"] == digest:
            return entry["file_token"]
        print(f"Uploading {name}", flush=True)
        token = self.api().upload(path)
        uploads[name] = {"sha256": digest, "file_token": token}
        self.save()
        return token

    def process(self, command, log, env=None, allow_nonzero=False):
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as stream:
            try:
                result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT,
                                        env=env, timeout=self.config["process_timeout"], check=False)
            except subprocess.TimeoutExpired:
                raise PipelineError(f"Process timed out. See {log}") from None
        if result.returncode and not allow_nonzero:
            raise PipelineError(f"Process exited {result.returncode}. See {log}")
        return result.returncode

    def blender(self, source, stage="blender", require_rig=False, yaw=True):
        entry = self.manifest["stages"].setdefault(stage, {})
        if entry.get("status") == "success":
            return self.artifact(entry)
        folder = self.directory / stage
        folder.mkdir(parents=True, exist_ok=True)
        output, report = folder / "character.glb", folder / "validation.json"
        report.unlink(missing_ok=True)
        args = [executable(self.config["blender"]["executable"]), "--background", "--factory-startup",
                "--python-exit-code", "1", "--python", str(ROOT / "scripts" / "blender" / "process.py"), "--",
                "--input", str(source), "--output", str(output), "--report", str(report),
                "--name", self.config["name"], "--height-m", str(self.config["blender"]["height_m"]),
                "--yaw-degrees", str(self.config["blender"]["yaw_degrees"] if yaw else 0)]
        if require_rig:
            args += ["--require-rig"]
        entry["status"] = "running"
        self.save()
        print(f"{stage}: normalizing {source.name}", flush=True)
        self.process(args, self.directory / "logs" / (stage + ".log"))
        if not report.is_file() or not read_json(report).get("success") or not output.is_file():
            raise PipelineError(f"Blender did not produce a successful report: {report}")
        entry.update(status="success", report=report.relative_to(self.directory).as_posix())
        self.record_artifact(entry, output)
        return output

    def convert_rig(self, source):
        cfg = self.config["output_rig"]
        if cfg["profile"] in ("source", "mixamo"):
            return source, read_json(self.directory / "blender" / "validation.json")
        entry = self.manifest["stages"].setdefault("target_rig", {})
        folder = self.directory / "target_rig"
        report, output = folder / "validation.json", folder / "character.glb"
        if entry.get("status") == "success":
            return self.artifact(entry), read_json(report)
        folder.mkdir(parents=True, exist_ok=True)
        report.unlink(missing_ok=True)
        command = [executable(self.config["blender"]["executable"]), "--background", "--factory-startup", "--python-exit-code", "1",
                   "--python", str(ROOT / "scripts" / "blender" / "target_rig.py"), "--", "--input", str(source),
                   "--reference", cfg["reference"], "--output", str(output), "--report", str(report),
                   "--profile", cfg["profile"], "--weights", cfg["weights"]]
        entry["status"] = "running"
        self.save()
        self.process(command, self.directory / "logs" / "target_rig.log")
        if not report.is_file() or not read_json(report).get("success"):
            raise PipelineError("Target rig conversion did not pass validation; see logs/target_rig.log.")
        entry.update(status="success", report=report.relative_to(self.directory).as_posix())
        self.record_artifact(entry, output)
        return output, read_json(report)

    def unreal(self, source, require_rig):
        entry = self.manifest["stages"].setdefault("unreal", {})
        if entry.get("status") in ("success", "imported_with_engine_errors"):
            report = self.directory / entry["report"]
            if not report.exists() or read_json(report).get("status") != "success":
                raise PipelineError("Unreal report missing from resumed build.")
            return
        cfg = self.config["unreal"]
        folder = self.directory / "unreal"
        report, job = folder / "import-report.json", folder / "job.json"
        report.unlink(missing_ok=True)
        write_json(job, {"glb": str(source), "name": self.config["name"],
                         "destination": cfg["destination"] + "/" + self.config["name"],
                         "report": str(report), "replace_existing": cfg["replace_existing"],
                         "expected_skeletal": require_rig,
                         **({"expected_height_cm": self.manifest["canonical"]["height_m"] * 100} if require_rig else {}),
                         "target": self.config["target"]})
        env = os.environ.copy()
        env["TRIPO_IMPORT_JOB"] = str(job)
        args = [executable(cfg["executable"]), str(Path(cfg["project"]).resolve()), "-run=pythonscript",
                "-script=" + str(ROOT / "scripts" / "unreal" / "import_character.py"), "-unattended", "-nosplash", "-NullRHI",
                "-EnablePlugins=PythonScriptPlugin,EditorScriptingUtilities,InterchangeEditor"]
        entry["status"] = "running"
        self.save()
        print("unreal: importing canonical GLB", flush=True)
        code = self.process(args, self.directory / "logs" / "unreal.log", env, allow_nonzero=True)
        if not report.is_file() or read_json(report).get("status") != "success":
            raise PipelineError(f"Unreal import did not report success: {report}")
        entry.update(status="success" if code == 0 else "imported_with_engine_errors",
                     process_exit_code=code, report=report.relative_to(self.directory).as_posix())
        if code:
            self.manifest.setdefault("warnings", []).append("Unreal saved and validated assets but exited nonzero; inspect logs/unreal.log for project startup errors.")
        self.save()

    def preflight(self, until):
        cfg = self.config
        # Fail before submitting billable jobs if a requested downstream tool is unavailable.
        if until != "tripo" or cfg["rig"]["mode"] != "none":
            executable(cfg["blender"]["executable"])
        if until == "unreal":
            executable(cfg["unreal"]["executable"])
            if not cfg["unreal"]["project"] or not Path(cfg["unreal"]["project"]).is_file():
                raise PipelineError("Unreal stage needs an existing --project .uproject file.")
        if not cfg["source"] or cfg["rig"]["mode"] != "none" or cfg["retopology"]["enabled"]:
            self.api()

    def run(self, until):
        self.preflight(until)
        self.directory.mkdir(parents=True, exist_ok=True)
        lock = self.directory / ".running"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise PipelineError("Build is locked by another process. If it crashed, verify it stopped before removing .running.") from None
        os.write(descriptor, str(os.getpid()).encode())
        os.close(descriptor)
        try:
            if self.path.exists():
                latest = read_json(self.path)
                if latest.get("fingerprint") != self.manifest.get("fingerprint"):
                    raise PipelineError("Build configuration changed while acquiring lock.")
                self.manifest = latest
            self.manifest["status"] = "running"
            self.save()
            cfg = self.config
            if cfg["source"]:
                entry = self.manifest["stages"].setdefault("source", {})
                source = self.artifact(entry)
                if not source:
                    original = Path(cfg["source"])
                    source = self.directory / "source" / original.name
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original, source)
                    sidecar_dir = original.with_suffix(".fbm")
                    if sidecar_dir.is_dir():
                        target_dir = source.with_suffix(".fbm")
                        shutil.copytree(sidecar_dir, target_dir, dirs_exist_ok=True)
                        entry["sidecars"] = [{"path": p.relative_to(self.directory).as_posix(), "sha256": sha256(p)}
                                             for p in target_dir.rglob("*") if p.is_file()]
                    entry["status"] = "success"
                    self.record_artifact(entry, source)
            else:
                payload = dict(cfg["tripo"])
                if payload["model"].startswith("P"):
                    payload.pop("geometry_quality")
                if set(cfg["inputs"]) == {"image"}:
                    token = self.upload("image", cfg["inputs"]["image"])
                    source = self.remote("generation", "/generation/image-to-model", {"input": token, **payload})
                else:
                    views = [{view: self.upload(view, cfg["inputs"][view])} for view in ("front", "left", "back", "right")]
                    source = self.remote("generation", "/generation/multiview-to-model", {"inputs": views, **payload})
            if cfg["retopology"]["enabled"]:
                token = self.upload("retopology_input", source)
                payload = {k: v for k, v in cfg["retopology"].items() if k != "enabled"}
                if payload["model"] == "v1.0":
                    payload.pop("bake")
                source = self.remote("retopology", "/mesh/decimate", {"input": token, **payload})
            require_rig = False
            if cfg["rig"]["mode"] != "none":
                # rig-check only accepts GLB, including after quad generation (FBX).
                if source.suffix.lower() != ".glb":
                    source = self.blender(source, "pre_rig", yaw=False)
                token = self.upload("rig_input", source)
                check = self.remote("rig_check", "/animations/rig-check", {"input": token}, model=False)
                if type(check.get("riggable")) is not bool:
                    raise PipelineError("rig-check omitted its boolean riggable result.")
                force = cfg["rig"]["mode"] == "force"
                if force or check["riggable"]:
                    rig_type = cfg["rig"]["type"] if force else check.get("rig_type")
                    if rig_type not in ("biped", "quadruped", "hexapod", "octopod", "avian", "serpentine", "aquatic"):
                        raise PipelineError("rig-check returned an unsupported rig type.")
                    source = self.remote("rig", "/animations/rig", {"input": token,
                        "model": "v1.0-20240301" if rig_type == "biped" else "v2.5-20260210",
                        "rig_type": rig_type, "spec": cfg["rig"]["spec"], "out_format": "glb"})
                    require_rig = True
                else:
                    self.manifest["rig_skipped"] = "rig-check reported non-riggable"
            if until != "tripo":
                source = self.blender(source, require_rig=require_rig)
                report = read_json(self.directory / "blender" / "validation.json")
                require_rig = bool(report.get("bones"))
                self.manifest["canonical"] = {"path": str(source.relative_to(self.directory)), "sha256": sha256(source),
                                               "format": "glTF 2.0 binary", "units": "meters", "up": "+Y",
                                               "rigged": require_rig, "bone_names_preserved": True}
                source, report = self.convert_rig(source)
                provenance = {k: {field: v[field] for field in ("task_id", "credits_consumed") if field in v}
                              for k,v in self.manifest["stages"].items() if v.get("task_id")}
                source, final = package(source, self.directory / "final", cfg["name"], cfg["output_rig"]["profile"], report, provenance)
                require_rig = final["rigged"]
                self.manifest["canonical"] = {"path": source.relative_to(self.directory).as_posix(), "sha256": final["sha256"],
                                               "rigged": require_rig, "height_m": final["height_m"], "rig_profile": final["rig_profile"]}
            if until == "unreal":
                self.unreal(source, require_rig)
            self.manifest.update(status="complete_with_warnings" if self.manifest.get("warnings") else "complete", completed_through=until)
            self.manifest.pop("error", None)
            self.save()
            return self.path
        except BaseException as exc:
            self.manifest.update(status="failed", error=str(exc) if isinstance(exc, PipelineError) else type(exc).__name__)
            self.save()
            raise
        finally:
            lock.unlink(missing_ok=True)
