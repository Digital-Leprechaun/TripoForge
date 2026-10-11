# Copyright (C) 2026 - Joseph M Wilcox
"""Tripo v3 REST client. Never retries a potentially billable POST."""
import json
import math
import mimetypes
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

from .common import PipelineError


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class RejectedRequest(PipelineError):
    """An explicit API rejection, as distinct from an uncertain transport outcome."""


class TripoClient:
    BASE_URL = "https://openapi.tripo3d.ai/v3"

    @classmethod
    def from_config(cls, config):
        key = os.environ.get(config["key_env"])
        if config.get("key_file"):
            key = Path(config["key_file"]).read_text(encoding="utf-8-sig").strip()
        return cls(key, task_timeout=config["task_timeout"], poll_interval=config["poll_interval"])

    def __init__(self, api_key, timeout=120, poll_interval=5, task_timeout=3600,
                 opener=None, sleep=time.sleep, clock=time.monotonic):
        if not api_key:
            raise PipelineError("Set TRIPO_API_KEY (or the configured key environment variable).")
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.task_timeout = task_timeout
        self.opener = opener or build_opener(NoRedirect()).open
        self.download_opener = opener or urlopen
        self.sleep, self.clock = sleep, clock

    def request(self, method, endpoint, body=None, content_type="application/json"):
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": content_type}
        request = Request(self.BASE_URL + endpoint, data=body, headers=headers, method=method)
        for attempt in range(4 if method == "GET" else 1):
            try:
                with self.opener(request, timeout=self.timeout) as response:
                    result = json.load(response)
                if not isinstance(result, dict):
                    raise PipelineError("Tripo response must be a JSON object; submission may have succeeded.")
                if result.get("code") != 0 or not isinstance(result.get("data"), dict):
                    raise PipelineError(f"Tripo {endpoint} returned API code {result.get('code')}. "
                                        "See Tripo task/account diagnostics; response text omitted to protect secrets.")
                return result["data"]
            except HTTPError as exc:
                if method == "GET" and exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    self.sleep(min(30, 2 ** attempt * 2))
                    continue
                detail = ""
                try:
                    data = json.loads(exc.read(16384))
                    if isinstance(data, dict):
                        detail = str(data.get("message", "")).replace(self.api_key, "[REDACTED]")[:500]
                except (ValueError, OSError):
                    pass
                error = RejectedRequest if 400 <= exc.code < 500 and exc.code != 408 else PipelineError
                raise error(f"Tripo {method} {endpoint}: HTTP {exc.code}. {detail} "
                            "POST requests are not automatically retried.") from None
            except (URLError, TimeoutError, OSError) as exc:
                if method == "GET" and attempt < 3:
                    self.sleep(2 ** attempt * 2)
                    continue
                raise PipelineError(f"Tripo {method} transport failure ({type(exc).__name__}). "
                                    "Submission may have succeeded; check the Tripo dashboard before resubmitting.") from None
            except (ValueError, KeyError):
                raise PipelineError("Tripo returned malformed JSON; submission may have succeeded.") from None

    def balance(self):
        data = self.request("GET", "/account/balance")
        for field in ("balance", "frozen"):
            value = data.get(field)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise PipelineError(f"Tripo balance response has an invalid {field} value.")
        return {field: data[field] for field in ("balance", "frozen")}

    def upload(self, path):
        path = Path(path)
        limit = 20 if path.suffix.lower() in (".png", ".jpg", ".jpeg") else 150
        if not 0 < path.stat().st_size <= limit * 1024 * 1024:
            raise PipelineError(f"Upload must be nonempty and <= {limit} MiB: {path.name}")
        boundary = "tripo_pipeline_" + uuid.uuid4().hex
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Fixed multipart filename prevents control characters in local paths becoming headers.
        prefix = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                  f"filename=\"upload{path.suffix.lower()}\"\r\nContent-Type: {mime}\r\n\r\n").encode()
        body = prefix + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        result = self.request("POST", "/files", body, "multipart/form-data; boundary=" + boundary)
        token = result.get("file_token")
        if not isinstance(token, str) or not token:
            raise PipelineError("Upload response has no file_token.")
        return token

    def submit(self, endpoint, payload):
        result = self.request("POST", endpoint, json.dumps(payload).encode())
        task_id = result.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise PipelineError("Submission response has no task_id; check dashboard before retrying.")
        return task_id

    def query(self, task_id):
        return self.request("GET", "/tasks/" + quote(task_id, safe=""))

    def wait(self, task_id, progress=lambda value: None):
        deadline = self.clock() + self.task_timeout
        while True:
            data = self.query(task_id)
            progress({k: data[k] for k in ("task_id", "status", "progress", "credits_consumed", "error_code") if k in data})
            status = data.get("status")
            if status == "success":
                return data
            if status in ("failed", "cancelled"):
                raise PipelineError(f"Tripo task {task_id}: {status}, code={data.get('error_code')}.")
            if status not in ("queued", "running"):
                raise PipelineError(f"Unknown Tripo task status: {status!r}")
            if self.clock() >= deadline:
                raise PipelineError(f"Timed out waiting for {task_id}; resume this build to keep polling.")
            self.sleep(min(self.poll_interval, max(0, deadline - self.clock())))

    def download_model(self, task, directory):
        url = task.get("output", {}).get("model_url")
        parsed = urlparse(url or "")
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise PipelineError("Task has no valid HTTPS model_url.")
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / "model.download"
        # Deliberately separate request: NEVER send the API key to the output CDN.
        try:
            with self.download_opener(Request(url), timeout=self.timeout) as response, temporary.open("wb") as stream:
                count = 0
                for block in iter(lambda: response.read(1024 * 1024), b""):
                    count += len(block)
                    if count > 1024 * 1024 * 1024:
                        raise PipelineError("Model download exceeded the 1 GiB safety limit.")
                    stream.write(block)
        except (HTTPError, URLError, OSError):
            raise PipelineError("Model download failed. Resume to query a fresh output URL.") from None
        with temporary.open("rb") as stream:
            header = stream.read(32)
        if header.startswith(b"glTF"):
            suffix = ".glb"
        elif header.startswith(b"Kaydara FBX Binary") or header.startswith(b"; FBX"):
            suffix = ".fbx"
        else:
            raise PipelineError("Downloaded model is not a recognized GLB/FBX. Refusing HTML, ZIP or incomplete data.")
        if suffix == ".glb":
            import struct
            if len(header) < 12 or struct.unpack_from("<II", header, 4) != (2, temporary.stat().st_size):
                raise PipelineError("Downloaded GLB has an invalid version/length.")
        destination = directory / ("model" + suffix)
        temporary.replace(destination)
        return destination
