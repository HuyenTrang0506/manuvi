"""Job queue: messages carry only a job id.

Interface (``Queue``): enqueue, receive (claim with visibility timeout), extend, ack, dead_letters.
Backends: ``DirQueue`` (local directory, claim by atomic rename) and ``SqsQueue`` (boto3, long polling).
A message received more than ``max_receives`` times without an ack is dead-lettered.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

from .model import valid_job_id


@dataclass
class Message:
    job_id: str
    receipt: str
    receive_count: int = 1


class Queue(Protocol):
    def enqueue(self, job_id: str) -> None: ...
    def receive(self, wait_seconds: float = 0, visibility_timeout: int = 300) -> Optional[Message]: ...
    def extend(self, msg: Message, seconds: int) -> None: ...
    def ack(self, msg: Message) -> None: ...
    def release(self, msg: Message) -> None: ...      # give the message back now (graceful stop)
    def dead_letters(self) -> list[str]: ...


class LostMessage(Exception):
    """The claim expired and someone else took the message."""


class DirQueue:
    """Directory queue. ``ready/`` -> ``inflight/`` by rename (atomic: exactly one claimer wins).

    File name: ``<enq_ms>.<job_id>.<receive_count>.<expiry_ms>.<token>`` (job ids contain no dots).
    An inflight file whose expiry passed is moved back to ``ready/`` (or ``dead/`` once it was
    received ``max_receives`` times) by whichever worker polls next.
    """

    def __init__(self, root: str | Path, max_receives: int = 3, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep):
        self.root = Path(root)
        self.max_receives, self.clock, self.sleep = max_receives, clock, sleep
        for d in ("ready", "inflight", "dead"):
            (self.root / d).mkdir(parents=True, exist_ok=True)

    def _ms(self) -> int:
        return int(self.clock() * 1000)

    @staticmethod
    def _parse(name: str) -> tuple[int, str, int, int, str]:
        enq, job, count, exp, tok = name.split(".")
        return int(enq), job, int(count), int(exp), tok

    def enqueue(self, job_id: str) -> None:
        valid_job_id(job_id)
        name = f"{self._ms():013d}.{job_id}.0.0.{secrets.token_hex(3)}"
        tmp = self.root / f".tmp-{name}"
        tmp.write_text(json.dumps({"job_id": job_id}), "utf-8")
        os.rename(tmp, self.root / "ready" / name)

    def _reap(self) -> None:
        now = self._ms()
        for p in sorted((self.root / "inflight").iterdir()):
            try:
                enq, job, count, exp, tok = self._parse(p.name)
            except ValueError:
                continue
            if exp > now:
                continue
            dest = "dead" if count >= self.max_receives else "ready"
            try:  # atomic: if another worker reaps/acks first this raises and we skip
                os.rename(p, self.root / dest / f"{enq:013d}.{job}.{count}.0.{tok}")
            except FileNotFoundError:
                pass

    def receive(self, wait_seconds: float = 0, visibility_timeout: int = 300) -> Optional[Message]:
        deadline = self.clock() + wait_seconds
        while True:
            self._reap()
            for p in sorted((self.root / "ready").iterdir()):
                try:
                    enq, job, count, _, tok = self._parse(p.name)
                except ValueError:
                    continue
                new = f"{enq:013d}.{job}.{count + 1}.{self._ms() + visibility_timeout * 1000}.{tok}"
                try:
                    os.rename(p, self.root / "inflight" / new)
                except FileNotFoundError:
                    continue  # lost the race
                return Message(job, new, count + 1)
            if self.clock() >= deadline:
                return None
            self.sleep(min(0.2, max(0.0, deadline - self.clock())))

    def _swap(self, msg: Message, dest_dir: str, dest_name: Optional[str]) -> None:
        src = self.root / "inflight" / msg.receipt
        try:
            if dest_name is None:
                os.unlink(src)
            else:
                os.rename(src, self.root / dest_dir / dest_name)
        except FileNotFoundError:
            raise LostMessage(msg.job_id)

    def extend(self, msg: Message, seconds: int) -> None:
        enq, job, count, _, tok = self._parse(msg.receipt)
        new = f"{enq:013d}.{job}.{count}.{self._ms() + seconds * 1000}.{tok}"
        self._swap(msg, "inflight", new)
        msg.receipt = new

    def ack(self, msg: Message) -> None:
        self._swap(msg, "inflight", None)

    def release(self, msg: Message) -> None:
        enq, job, count, _, tok = self._parse(msg.receipt)
        self._swap(msg, "ready", f"{enq:013d}.{job}.{max(count - 1, 0)}.0.{tok}")  # not a failed attempt

    def dead_letters(self) -> list[str]:
        return [self._parse(p.name)[1] for p in sorted((self.root / "dead").iterdir()) if "." in p.name]


class SqsQueue:
    """SQS standard queue. Message body is ``{"job_id": ...}`` and nothing else.

    Dead letters: configure a redrive policy on the queue (infra) and/or pass ``dlq_url``; in the
    latter case a message received more than ``max_receives`` times is copied to the DLQ and deleted.
    """

    def __init__(self, queue_url: str, client: Any = None, dlq_url: Optional[str] = None,
                 max_receives: int = 3, region: Optional[str] = None):
        if not queue_url:
            raise ValueError("MANUVI_QUEUE_URL is required for the sqs queue")
        if client is None:
            import boto3
            client = boto3.client("sqs", region_name=region)
        self.url, self.client, self.dlq_url, self.max_receives = queue_url, client, dlq_url, max_receives

    def enqueue(self, job_id: str) -> None:
        valid_job_id(job_id)
        self.client.send_message(QueueUrl=self.url, MessageBody=json.dumps({"job_id": job_id}))

    def receive(self, wait_seconds: float = 0, visibility_timeout: int = 300) -> Optional[Message]:
        while True:
            r = self.client.receive_message(
                QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=min(int(wait_seconds), 20),
                VisibilityTimeout=visibility_timeout, AttributeNames=["ApproximateReceiveCount"])
            msgs = r.get("Messages") or []
            if not msgs:
                return None
            m = msgs[0]
            count = int(m.get("Attributes", {}).get("ApproximateReceiveCount", "1"))
            try:
                job_id = valid_job_id(json.loads(m["Body"])["job_id"])
            except (ValueError, KeyError, TypeError):
                self._to_dlq(m)  # poison message
                continue
            if count > self.max_receives:
                self._to_dlq(m)
                continue
            return Message(job_id, m["ReceiptHandle"], count)

    def _to_dlq(self, m: dict) -> None:
        if self.dlq_url:
            self.client.send_message(QueueUrl=self.dlq_url, MessageBody=m["Body"][:2048])
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=m["ReceiptHandle"])

    def extend(self, msg: Message, seconds: int) -> None:
        self.client.change_message_visibility(QueueUrl=self.url, ReceiptHandle=msg.receipt,
                                              VisibilityTimeout=seconds)

    def ack(self, msg: Message) -> None:
        self.client.delete_message(QueueUrl=self.url, ReceiptHandle=msg.receipt)

    def release(self, msg: Message) -> None:
        self.client.change_message_visibility(QueueUrl=self.url, ReceiptHandle=msg.receipt, VisibilityTimeout=0)

    def dead_letters(self) -> list[str]:
        if not self.dlq_url:
            return []
        out: list[str] = []
        r = self.client.receive_message(QueueUrl=self.dlq_url, MaxNumberOfMessages=10, VisibilityTimeout=0)
        for m in r.get("Messages") or []:
            try:
                out.append(json.loads(m["Body"])["job_id"])
            except (ValueError, KeyError, TypeError):
                pass
        return out
