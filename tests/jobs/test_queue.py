import json
import threading

import boto3
import pytest
from moto import mock_aws

from src.jobs import DirQueue, LostMessage, SqsQueue

JID = "job123abc"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def dq(tmp_path, clock):
    return DirQueue(tmp_path / "q", max_receives=2, clock=clock, sleep=lambda s: None)


def test_dir_claim_ack(dq):
    dq.enqueue(JID)
    m = dq.receive(visibility_timeout=30)
    assert m.job_id == JID and m.receive_count == 1
    assert dq.receive() is None  # invisible while claimed
    dq.ack(m)
    assert dq.receive() is None


def test_dir_claim_is_exclusive(tmp_path):
    q = DirQueue(tmp_path / "q")
    for i in range(20):
        q.enqueue(f"job{i:04d}x")
    got, lock = [], threading.Lock()

    def work():
        while (m := DirQueue(tmp_path / "q").receive()) is not None:
            with lock:
                got.append(m.job_id)

    ts = [threading.Thread(target=work) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(got) == sorted(set(got)) and len(got) == 20


def test_dir_visibility_timeout_and_extend(dq, clock):
    dq.enqueue(JID)
    m = dq.receive(visibility_timeout=10)
    clock.t += 9
    dq.extend(m, 10)
    clock.t += 5  # past the original expiry, within the extension
    assert dq.receive() is None
    clock.t += 6
    m2 = dq.receive(visibility_timeout=10)
    assert m2.job_id == JID and m2.receive_count == 2
    with pytest.raises(LostMessage):
        dq.ack(m)  # stale receipt


def test_dir_dead_letter(dq, clock):
    dq.enqueue(JID)
    for _ in range(2):
        assert dq.receive(visibility_timeout=5) is not None
        clock.t += 6
    assert dq.receive() is None
    assert dq.dead_letters() == [JID]


def test_dir_release_is_not_a_failed_attempt(dq):
    dq.enqueue(JID)
    m = dq.receive()
    dq.release(m)
    assert dq.receive().receive_count == 1


def test_dir_rejects_bad_job_id(dq):
    with pytest.raises(ValueError):
        dq.enqueue("../etc/passwd")


@pytest.fixture
def sqs():
    with mock_aws():
        c = boto3.client("sqs", region_name="ap-southeast-2")
        dlq = c.create_queue(QueueName="dlq")["QueueUrl"]
        url = c.create_queue(QueueName="jobs")["QueueUrl"]
        yield c, url, dlq


def test_sqs_roundtrip_body_has_only_job_id(sqs):
    c, url, dlq = sqs
    q = SqsQueue(url, client=c, dlq_url=dlq)
    q.enqueue(JID)
    m = q.receive(wait_seconds=1, visibility_timeout=30)
    assert m.job_id == JID and m.receive_count == 1
    q.extend(m, 60)
    q.ack(m)
    assert q.receive() is None
    q.enqueue(JID)
    body = c.receive_message(QueueUrl=url)["Messages"][0]["Body"]
    assert json.loads(body) == {"job_id": JID}


def test_sqs_dead_letter_after_max_receives(sqs):
    c, url, dlq = sqs
    q = SqsQueue(url, client=c, dlq_url=dlq, max_receives=2)
    q.enqueue(JID)
    for _ in range(2):
        m = q.receive(visibility_timeout=0)
        assert m is not None
    assert q.receive(visibility_timeout=0) is None  # third receive: moved to the DLQ
    assert q.dead_letters() == [JID]


def test_sqs_poison_message_goes_to_dlq(sqs):
    c, url, dlq = sqs
    q = SqsQueue(url, client=c, dlq_url=dlq)
    c.send_message(QueueUrl=url, MessageBody="not json")
    assert q.receive() is None
    assert c.receive_message(QueueUrl=dlq).get("Messages")


def test_sqs_release_makes_message_visible(sqs):
    c, url, _ = sqs
    q = SqsQueue(url, client=c)
    q.enqueue(JID)
    m = q.receive(visibility_timeout=300)
    assert q.receive() is None
    q.release(m)
    assert q.receive().job_id == JID
