"""Job file store: ``jobs/<id>/<name>``. Backends: ``LocalStore`` (directory) and ``S3Store`` (private bucket).

Names are validated (no absolute paths, no ``..``) before they touch a path or a key.
Files: input.pdf, job.json, status.json, cancel.json, chunks.json, layout.json, segments.json, terms.json,
audit.jsonl, state/*.sqlite (resume checkpoints), out/*.pdf|xlsx (downloads).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Optional, Protocol

from .model import JobRecord, JobStatus, valid_job_id

NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,80}(/[A-Za-z0-9_-][A-Za-z0-9._-]{0,80}){0,2}$")


def valid_name(name: str) -> str:
    if not isinstance(name, str) or not NAME_RE.match(name) or ".." in name.split("/"):
        raise ValueError("bad file name")
    return name


class Store(Protocol):
    def put_bytes(self, job_id: str, name: str, data: bytes) -> None: ...
    def put_file(self, job_id: str, name: str, path: Path) -> None: ...
    def get_bytes(self, job_id: str, name: str) -> bytes: ...
    def get_file(self, job_id: str, name: str, dest: Path) -> None: ...
    def exists(self, job_id: str, name: str) -> bool: ...
    def list_names(self, job_id: str, prefix: str = "") -> list[str]: ...
    def list_job_ids(self) -> list[str]: ...
    def download_url(self, job_id: str, name: str, filename: str, expires: int = 300) -> Optional[str]: ...


class _JsonMixin:
    """Typed helpers shared by both backends."""

    def get_job(self, job_id: str) -> JobRecord:
        return JobRecord.model_validate_json(self.get_bytes(job_id, "job.json"))

    def put_job(self, rec: JobRecord) -> None:
        self.put_bytes(rec.id, "job.json", rec.model_dump_json().encode())

    def get_status(self, job_id: str) -> JobStatus:
        try:
            return JobStatus.model_validate_json(self.get_bytes(job_id, "status.json"))
        except (FileNotFoundError, KeyError):
            return JobStatus()

    def put_status(self, job_id: str, st: JobStatus) -> None:
        self.put_bytes(job_id, "status.json", st.model_dump_json().encode())

    def is_cancelled(self, job_id: str) -> bool:
        return self.exists(job_id, "cancel.json")

    def request_cancel(self, job_id: str, by: str) -> None:
        self.put_bytes(job_id, "cancel.json", json.dumps({"by": by}).encode())

    def list_jobs(self) -> list[tuple[JobRecord, JobStatus]]:
        out = []
        for jid in self.list_job_ids():
            try:
                out.append((self.get_job(jid), self.get_status(jid)))
            except (FileNotFoundError, KeyError, ValueError):
                continue
        return sorted(out, key=lambda x: x[0].created_at, reverse=True)


class LocalStore(_JsonMixin):
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        (self.root / "jobs").mkdir(parents=True, exist_ok=True)

    def _path(self, job_id: str, name: str) -> Path:
        p = (self.root / "jobs" / valid_job_id(job_id) / valid_name(name)).resolve()
        if not p.is_relative_to(self.root / "jobs" / job_id):
            raise ValueError("path escapes the job directory")
        return p

    def put_bytes(self, job_id, name, data):
        p = self._path(job_id, name)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def put_file(self, job_id, name, path):
        self.put_bytes(job_id, name, Path(path).read_bytes())

    def get_bytes(self, job_id, name):
        return self._path(job_id, name).read_bytes()

    def get_file(self, job_id, name, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(job_id, name))

    def exists(self, job_id, name):
        return self._path(job_id, name).is_file()

    def list_names(self, job_id, prefix=""):
        base = self.root / "jobs" / valid_job_id(job_id)
        if not base.is_dir():
            return []
        return sorted(str(p.relative_to(base)) for p in base.rglob("*")
                      if p.is_file() and not p.name.startswith(".tmp-") and str(p.relative_to(base)).startswith(prefix))

    def list_job_ids(self):
        return sorted(p.name for p in (self.root / "jobs").iterdir() if p.is_dir())

    def download_url(self, job_id, name, filename, expires=300):
        return None  # served by the web app from local disk

    def local_path(self, job_id: str, name: str) -> Path:
        return self._path(job_id, name)


class S3Store(_JsonMixin):
    """Private bucket, ``jobs/<id>/`` prefix, server-side encryption on every write, short-lived presigned GETs."""

    def __init__(self, bucket: str, client: Any = None, prefix: str = "jobs", sse: str = "AES256",
                 kms_key_id: Optional[str] = None, region: Optional[str] = None):
        if not bucket:
            raise ValueError("MANUVI_S3_BUCKET is required for the s3 store")
        if client is None:
            import boto3
            from botocore.config import Config
            client = boto3.client("s3", region_name=region, config=Config(signature_version="s3v4"))
        self.bucket, self.client, self.prefix = bucket, client, prefix.strip("/")
        self.sse, self.kms_key_id = sse, kms_key_id

    def _key(self, job_id: str, name: str) -> str:
        return f"{self.prefix}/{valid_job_id(job_id)}/{valid_name(name)}"

    def _sse(self) -> dict:
        kw = {"ServerSideEncryption": self.sse}
        if self.sse == "aws:kms" and self.kms_key_id:
            kw["SSEKMSKeyId"] = self.kms_key_id
        return kw

    def put_bytes(self, job_id, name, data):
        self.client.put_object(Bucket=self.bucket, Key=self._key(job_id, name), Body=data, **self._sse())

    def put_file(self, job_id, name, path):
        self.client.upload_file(str(path), self.bucket, self._key(job_id, name), ExtraArgs=self._sse())

    def get_bytes(self, job_id, name):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=self._key(job_id, name))["Body"].read()
        except self.client.exceptions.NoSuchKey:
            raise FileNotFoundError(name)

    def get_file(self, job_id, name, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.get_bytes(job_id, name))

    def exists(self, job_id, name):
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(job_id, name))
            return True
        except Exception as e:  # botocore ClientError 404
            if getattr(e, "response", {}).get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list_names(self, job_id, prefix=""):
        base = f"{self.prefix}/{valid_job_id(job_id)}/"
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": base + prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.client.list_objects_v2(**kw)
            out += [o["Key"][len(base):] for o in r.get("Contents", [])]
            token = r.get("NextContinuationToken")
            if not token:
                return sorted(out)

    def list_job_ids(self):
        out, token = [], None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": self.prefix + "/", "Delimiter": "/"}
            if token:
                kw["ContinuationToken"] = token
            r = self.client.list_objects_v2(**kw)
            out += [p["Prefix"][len(self.prefix) + 1:].strip("/") for p in r.get("CommonPrefixes", [])]
            token = r.get("NextContinuationToken")
            if not token:
                return sorted(j for j in out if j)

    def download_url(self, job_id, name, filename, expires=300):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:80] or "download"
        return self.client.generate_presigned_url(
            "get_object", ExpiresIn=min(int(expires), 900),
            Params={"Bucket": self.bucket, "Key": self._key(job_id, name),
                    "ResponseContentDisposition": f'attachment; filename="{safe}"'})
