"""Backend selection from the environment.

MANUVI_QUEUE=dir|sqs   MANUVI_QUEUE_URL (dir: queue directory; sqs: queue URL)   MANUVI_DLQ_URL (optional)
MANUVI_STORE=local|s3  MANUVI_S3_BUCKET   MANUVI_DATA_DIR (default workspace/data)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from .queue import DirQueue, Queue, SqsQueue
from .store import LocalStore, S3Store, Store


def data_dir(env: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("MANUVI_DATA_DIR") or "workspace/data")


def make_queue(env: Optional[Mapping[str, str]] = None) -> Queue:
    env = os.environ if env is None else env
    kind = env.get("MANUVI_QUEUE", "dir")
    if kind == "dir":
        return DirQueue(env.get("MANUVI_QUEUE_URL") or data_dir(env) / "queue")
    if kind == "sqs":
        return SqsQueue(env.get("MANUVI_QUEUE_URL", ""), dlq_url=env.get("MANUVI_DLQ_URL") or None)
    raise ValueError(f"MANUVI_QUEUE must be dir or sqs, got {kind!r}")


def make_store(env: Optional[Mapping[str, str]] = None) -> Store:
    env = os.environ if env is None else env
    kind = env.get("MANUVI_STORE", "local")
    if kind == "local":
        return LocalStore(data_dir(env) / "store")
    if kind == "s3":
        return S3Store(env.get("MANUVI_S3_BUCKET", ""), kms_key_id=env.get("MANUVI_S3_KMS_KEY") or None,
                       sse="aws:kms" if env.get("MANUVI_S3_KMS_KEY") else "AES256")
    raise ValueError(f"MANUVI_STORE must be local or s3, got {kind!r}")
