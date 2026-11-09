"""Offline checks of infra/manuvi-hosting.yaml (no AWS access needed)."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "infra" / "manuvi-hosting.yaml"


class _CfnLoader(yaml.SafeLoader):
    pass


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)
    key = "Ref" if suffix == "Ref" else f"Fn::{suffix}"
    return {key: value}


_CfnLoader.add_multi_constructor("!", _tag)


@pytest.fixture(scope="module")
def tpl():
    return yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=_CfnLoader)


def _resources(tpl, rtype):
    return {k: v for k, v in tpl["Resources"].items() if v["Type"] == rtype}


def _statements(tpl):
    for res in _resources(tpl, "AWS::IAM::Role").values():
        for pol in res["Properties"].get("Policies", []):
            yield from pol["PolicyDocument"]["Statement"]


def test_no_ssh_port_and_only_80_443(tpl):
    (sg,) = _resources(tpl, "AWS::EC2::SecurityGroup").values()
    ports = set()
    for rule in sg["Properties"]["SecurityGroupIngress"]:
        assert rule["FromPort"] <= rule["ToPort"]
        ports.update(range(rule["FromPort"], rule["ToPort"] + 1))
    assert ports == {80, 443}
    assert 22 not in ports


def test_no_ssh_anywhere_in_template(tpl):
    text = TEMPLATE.read_text(encoding="utf-8").lower()
    assert "keyname" not in text
    assert "port 22" not in text and "fromport: 22" not in text


def test_ssm_session_manager_enabled(tpl):
    role = tpl["Resources"]["WebInstanceRole"]["Properties"]
    assert any(
        "AmazonSSMManagedInstanceCore" in json.dumps(a) for a in role["ManagedPolicyArns"]
    )


def test_no_wildcard_iam_actions(tpl):
    count = 0
    for st in _statements(tpl):
        if st["Effect"] != "Allow":
            continue
        actions = st["Action"] if isinstance(st["Action"], list) else [st["Action"]]
        for a in actions:
            count += 1
            assert "*" not in a, a
    assert count > 0


def test_iam_resources_are_not_bare_wildcards(tpl):
    for st in _statements(tpl):
        res = st["Resource"] if isinstance(st["Resource"], list) else [st["Resource"]]
        assert "*" not in res


def test_queue_has_dlq_and_sse(tpl):
    queues = _resources(tpl, "AWS::SQS::Queue")
    assert len(queues) == 2
    main = tpl["Resources"]["JobQueue"]["Properties"]
    assert "deadLetterTargetArn" in main["RedrivePolicy"]
    assert main["RedrivePolicy"]["maxReceiveCount"] >= 1
    assert main["VisibilityTimeout"] == 900
    for q in queues.values():
        assert q["Properties"]["SqsManagedSseEnabled"] is True
        assert q["Properties"]["MessageRetentionPeriod"] >= 345600


def test_worker_role_scope(tpl):
    role = tpl["Resources"]["WorkerRole"]["Properties"]
    assert role["RoleName"] == "manuvi-worker"
    assert role["AssumeRolePolicyDocument"]["Statement"][0]["Principal"] == {
        "AWS": {"Ref": "TrustedPrincipalArn"}
    }
    sts = role["Policies"][0]["PolicyDocument"]["Statement"]
    actions = {a for st in sts for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])}
    assert {"sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"} <= actions
    assert "sqs:SendMessage" not in actions
    s3 = json.dumps([st["Resource"] for st in sts if any(a.startswith("s3:") for a in st["Action"])])
    assert "/jobs/*" in s3 and "/traces/*" in s3


def test_instance_role_s3_limited_to_jobs_prefix(tpl):
    sts = tpl["Resources"]["WebInstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    for st in sts:
        if "s3:GetObject" in st["Action"]:
            assert "/jobs/*" in json.dumps(st["Resource"])


def test_imds_v2_and_encrypted_volume(tpl):
    data = tpl["Resources"]["WebLaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    assert data["BlockDeviceMappings"][0]["Ebs"]["Encrypted"] is True


def test_elastic_ip_attached(tpl):
    (eip,) = _resources(tpl, "AWS::EC2::EIP").values()
    assert eip["Properties"]["InstanceId"] == {"Ref": "WebInstance"}


def test_no_plaintext_secret_parameters_or_defaults(tpl):
    for name, p in tpl["Parameters"].items():
        low = name.lower()
        if any(w in low for w in ("password", "secret", "token", "key", "hash")):
            assert "Default" not in p, name
        assert "Default" not in p or "arn:" not in str(p["Default"]), name
    assert "NoEcho" not in json.dumps(tpl)  # secrets must not be template parameters at all


def test_userdata_has_no_secrets(tpl):
    ud = json.dumps(tpl["Resources"]["WebInstance"]["Properties"]["UserData"]).lower()
    for bad in ("password", "secret", "token", "akia", "hash"):
        assert bad not in ud, bad
    assert "/manuvi/prod/web/users" in ud  # only the parameter NAME, read later at start


def test_ami_from_public_ssm_parameter(tpl):
    img = json.dumps(tpl["Resources"]["WebLaunchTemplate"]["Properties"]["LaunchTemplateData"]["ImageId"])
    assert "resolve:ssm:/aws/service/ami-amazon-linux-latest/al2023" in img


def test_dockerfile_is_non_root_multistage():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.count("FROM ") >= 2
    assert "python:3.13-slim" in text
    assert "USER manuvi" in text
    for bad in ("ARG PASSWORD", "ARG TOKEN", "AWS_SECRET"):
        assert bad not in text


def test_dockerignore_excludes_private_dirs():
    lines = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for must in ("data/", "workspace/", ".git/"):
        assert must in lines
