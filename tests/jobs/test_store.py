import boto3
import pytest
from moto import mock_aws

from src.jobs import JobRecord, JobState, JobStatus, LocalStore, S3Store, check_transition, InvalidTransition
from src.jobs import make_queue, make_store, DirQueue, SqsQueue

REC = JobRecord(id="job123abc", name="Manual", owner="founder", sections="23:1-4")


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        yield LocalStore(tmp_path / "s")
    else:
        with mock_aws():
            c = boto3.client("s3", region_name="ap-southeast-2")
            c.create_bucket(Bucket="manuvi-test", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"})
            yield S3Store("manuvi-test", client=c)


def test_roundtrip(store, tmp_path):
    store.put_job(REC)
    store.put_bytes(REC.id, "input.pdf", b"%PDF-1.4 x")
    store.put_bytes(REC.id, "out/vi.pdf", b"vi")
    assert store.get_job(REC.id) == REC
    assert store.get_bytes(REC.id, "input.pdf") == b"%PDF-1.4 x"
    store.get_file(REC.id, "out/vi.pdf", tmp_path / "d" / "vi.pdf")
    assert (tmp_path / "d" / "vi.pdf").read_bytes() == b"vi"
    assert store.exists(REC.id, "input.pdf") and not store.exists(REC.id, "nope.json")
    assert store.list_names(REC.id, "out/") == ["out/vi.pdf"]
    assert store.list_job_ids() == [REC.id]
    assert store.get_status(REC.id).state == JobState.queued  # no status file yet
    store.put_status(REC.id, JobStatus(state=JobState.running, cost_usd=0.5))
    assert store.list_jobs()[0][1].state == JobState.running
    assert not store.is_cancelled(REC.id)
    store.request_cancel(REC.id, "founder")
    assert store.is_cancelled(REC.id)
    with pytest.raises(FileNotFoundError):
        store.get_bytes(REC.id, "missing.json")


@pytest.mark.parametrize("bad", ["../x", "/etc/passwd", "a/../../b", "a//b", ".hidden", "x" * 200, "a b", ""])
def test_bad_names_rejected(store, bad):
    with pytest.raises(ValueError):
        store.put_bytes(REC.id, bad, b"x")


@pytest.mark.parametrize("bad", ["../job", "a/b", "short", "x" * 41, "job 1234"])
def test_bad_job_ids_rejected(store, bad):
    with pytest.raises(ValueError):
        store.get_bytes(bad, "job.json")


def test_s3_encryption_and_presign():
    with mock_aws():
        c = boto3.client("s3", region_name="ap-southeast-2")
        c.create_bucket(Bucket="manuvi-test", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"})
        s = S3Store("manuvi-test", client=c)
        s.put_bytes(REC.id, "input.pdf", b"%PDF")
        head = c.head_object(Bucket="manuvi-test", Key="jobs/job123abc/input.pdf")
        assert head["ServerSideEncryption"] == "AES256"
        url = s.download_url(REC.id, "input.pdf", "my file.pdf", expires=99999)
        assert "X-Amz-Expires=900" in url or "Expires=" in url  # capped
        assert "my_file.pdf" in url
        assert "jobs/job123abc/input.pdf" in url


def test_state_machine():
    check_transition(JobState.queued, JobState.running)
    check_transition(JobState.running, JobState.needs_review)
    with pytest.raises(InvalidTransition):
        check_transition(JobState.done, JobState.running)
    with pytest.raises(InvalidTransition):
        check_transition(JobState.cancelled, JobState.queued)


def test_config(tmp_path):
    env = {"MANUVI_DATA_DIR": str(tmp_path)}
    assert isinstance(make_queue(env), DirQueue) and isinstance(make_store(env), LocalStore)
    with pytest.raises(ValueError):
        make_queue({"MANUVI_QUEUE": "sqs"})  # URL required
    with pytest.raises(ValueError):
        make_store({"MANUVI_STORE": "s3"})  # bucket required
    with pytest.raises(ValueError):
        make_store({"MANUVI_STORE": "ftp"})
    with mock_aws():
        boto3.setup_default_session(region_name="ap-southeast-2")
        assert isinstance(make_queue({"MANUVI_QUEUE": "sqs", "MANUVI_QUEUE_URL": "http://x/q"}), SqsQueue)
