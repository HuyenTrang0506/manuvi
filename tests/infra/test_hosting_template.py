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
            # jobs/ for the job files, portal/audit/ for the portal audit log; nothing else in the bucket
            assert any(p in json.dumps(st["Resource"]) for p in ("/jobs/*", "/portal/audit/*"))


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


def test_env_file_selects_sqs_and_s3_backends(tpl):
    """src.jobs.config defaults to dir/local: the hosted env must select sqs/s3 explicitly."""
    ud = json.dumps(tpl["Resources"]["WebInstance"]["Properties"]["UserData"])
    for line in ("MANUVI_QUEUE=sqs", "MANUVI_STORE=s3", "MANUVI_COOKIE_SECURE=1", "MANUVI_QUEUE_URL=",
                 "MANUVI_S3_BUCKET=", "MANUVI_DLQ_URL=", "MANUVI_USERS_PARAM="):
        assert line in ud, line


def test_web_role_is_least_privilege(tpl):
    sts = tpl["Resources"]["WebInstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    actions = {a for st in sts for a in (st["Action"] if isinstance(st["Action"], list) else [st["Action"]])}
    assert "sqs:ReceiveMessage" not in actions and "sqs:DeleteMessage" not in actions
    ssm = json.dumps([st["Resource"] for st in sts if "ssm:GetParameter" in st["Action"]])
    assert "/manuvi/*" not in ssm and "/manuvi/prod/web/users" in ssm


def test_roles_can_list_bucket_for_404s(tpl):
    """HeadObject/GetObject on a missing key return 403 (not 404) without s3:ListBucket on the bucket;
    a s3:prefix condition would not apply to those calls, so none may be set."""
    for role in ("WebInstanceRole", "WorkerRole"):
        sts = tpl["Resources"][role]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
        (lb,) = [s for s in sts if s["Action"] == "s3:ListBucket"]
        assert "Condition" not in lb


def test_compose_passes_allowed_hosts_and_start_computes_it():
    assert "MANUVI_ALLOWED_HOSTS" in (ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "MANUVI_ALLOWED_HOSTS" in (ROOT / "deploy" / "start.sh").read_text(encoding="utf-8")


def test_audit_prefix_is_writable_by_the_web_role_only_and_never_deletable(tpl):
    web = tpl["Resources"]["WebInstanceRole"]["Properties"]["Policies"][0]["PolicyDocument"]["Statement"]
    (st,) = [s for s in web if s.get("Sid") == "PortalAuditObjects"]
    assert set(st["Action"]) == {"s3:GetObject", "s3:PutObject"}
    assert "/portal/audit/*" in json.dumps(st["Resource"])
    for role in ("WebInstanceRole", "WorkerRole"):
        text = json.dumps(tpl["Resources"][role]["Properties"]["Policies"])
        assert "s3:DeleteObject" not in text
    assert "portal/" not in json.dumps(tpl["Resources"]["WorkerRole"]["Properties"]["Policies"])


def test_termination_protection_is_a_parameter_defaulting_to_true(tpl):
    p = tpl["Parameters"]["DisableApiTermination"]
    assert p["Default"] == "true" and p["AllowedValues"] == ["true", "false"]
    assert tpl["Resources"]["WebInstance"]["Properties"]["DisableApiTermination"] == {"Ref": "DisableApiTermination"}


def test_source_tarball_is_verified_before_it_is_unpacked(tpl):
    p = tpl["Parameters"]["SourceSha256"]
    assert "Default" not in p                                       # no default: the deployer must supply it
    import re
    rx = re.compile(p["AllowedPattern"])
    assert rx.match("a" * 64) and not rx.match("a" * 63) and not rx.match("A" * 64) and not rx.match("g" * 64)
    ud = json.dumps(tpl["Resources"]["WebInstance"]["Properties"]["UserData"])
    assert ud.index("sha256sum -c") < ud.index("tar -xzf") and "SourceSha256" in ud
    assert "| tar" not in ud                                        # no more curl | tar (unverified stream)
    assert "set -euo pipefail" in ud
    sh = (ROOT / "scripts" / "aws_deploy_hosting.sh").read_text(encoding="utf-8")
    assert "SOURCE_SHA256:?" in sh and "SourceSha256=" in sh


def test_on_demand_tls_asks_the_app_first_and_the_ask_route_is_not_public():
    internal = (ROOT / "deploy" / "Caddyfile.internal").read_text(encoding="utf-8")
    assert "ask http://app:8000/internal/tls-ask" in internal and "on_demand" in internal
    for name in ("Caddyfile", "Caddyfile.internal"):
        text = (ROOT / "deploy" / name).read_text(encoding="utf-8")
        assert text.count("respond @internal 404") == text.count("reverse_proxy app:8000")   # every site hides it


def test_uvicorn_trusts_forwarded_headers_only_from_the_caddy_container():
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "forwarded-allow-ips '*'" not in docker and "forwarded-allow-ips='*'" not in docker
    assert "MANUVI_FORWARDED_ALLOW_IPS" in docker
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
    caddy_ip = compose["services"]["caddy"]["networks"]["default"]["ipv4_address"]
    assert compose["services"]["app"]["environment"]["MANUVI_FORWARDED_ALLOW_IPS"] == caddy_ip
    net = compose["networks"]["default"]["ipam"]["config"][0]["subnet"]
    import ipaddress
    assert ipaddress.ip_address(caddy_ip) in ipaddress.ip_network(net)
    assert compose["services"]["app"]["environment"]["MANUVI_TRUSTED_PROXIES"] == net
