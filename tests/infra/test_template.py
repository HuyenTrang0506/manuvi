"""Offline checks of infra/manuvi-foundation.yaml (no AWS access needed)."""

from pathlib import Path

import pytest
import yaml

TEMPLATE = Path(__file__).resolve().parents[2] / "infra" / "manuvi-foundation.yaml"


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


def test_bucket_blocks_public_access_and_is_retained(tpl):
    (bucket,) = _resources(tpl, "AWS::S3::Bucket").values()
    block = bucket["Properties"]["PublicAccessBlockConfiguration"]
    assert all(block[k] is True for k in (
        "BlockPublicAcls", "BlockPublicPolicy", "IgnorePublicAcls", "RestrictPublicBuckets"))
    assert bucket["DeletionPolicy"] == "Retain"
    assert bucket["Properties"]["VersioningConfiguration"]["Status"] == "Enabled"
    assert "WebsiteConfiguration" not in bucket["Properties"]


def test_bucket_policy_denies_non_tls(tpl):
    (pol,) = _resources(tpl, "AWS::S3::BucketPolicy").values()
    stmts = pol["Properties"]["PolicyDocument"]["Statement"]
    assert any(
        s["Effect"] == "Deny" and s["Condition"]["Bool"]["aws:SecureTransport"] == "false"
        for s in stmts)


def test_iam_policy_has_no_wildcard_allow(tpl):
    (pol,) = _resources(tpl, "AWS::IAM::ManagedPolicy").values()
    for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
        if isinstance(stmt, dict) and "Fn::If" in stmt:
            stmt = stmt["Fn::If"][1]
        actions = stmt["Action"] if isinstance(stmt["Action"], list) else [stmt["Action"]]
        if stmt["Effect"] == "Allow":
            for a in actions:
                assert a != "*" and not a.endswith(":*"), a
                assert not a.startswith("iam:")
            assert stmt["Resource"] != "*"
        else:
            assert stmt["Condition"]["StringNotEquals"]["aws:RequestedRegion"]


def test_ssm_names_follow_contract(tpl):
    params = {v["Properties"]["Name"]: v["Properties"] for v in _resources(tpl, "AWS::SSM::Parameter").values()}
    expected = {}
    for prof in ("dev", "qa", "prod"):
        for leaf in ("model/bulk", "model/strong", "model/vision", "allow_paid", "cap/job_usd", "cap/month_usd"):
            expected[f"/manuvi/{prof}/{leaf}"] = None
    assert set(params) == set(expected)
    assert all(p["Tier"] == "Standard" for p in params.values())


def test_defaults(tpl):
    p = {v["Properties"]["Name"]: v["Properties"]["Value"] for v in _resources(tpl, "AWS::SSM::Parameter").values()}
    for prof in ("dev", "qa", "prod"):
        assert p[f"/manuvi/{prof}/allow_paid"] == "false"
    assert p["/manuvi/dev/model/bulk"] == "ollama:qwen2.5:14b"
    assert p["/manuvi/qa/model/bulk"] == "bedrock:amazon.nova-lite-v1:0"
    assert p["/manuvi/prod/model/strong"] == "none"
    caps = [(p[f"/manuvi/{x}/cap/job_usd"], p[f"/manuvi/{x}/cap/month_usd"]) for x in ("dev", "qa", "prod")]
    assert caps == [("0", "0"), ("1", "3"), ("5", "15")]


def test_log_group_and_budget(tpl):
    (lg,) = _resources(tpl, "AWS::Logs::LogGroup").values()
    assert lg["Properties"]["LogGroupName"] == "/manuvi/app"
    assert lg["Properties"]["RetentionInDays"] == 30
    assert tpl["Parameters"]["MonthlyBudgetUsd"]["Default"] == 20
    (b,) = _resources(tpl, "AWS::Budgets::Budget").values()
    thresholds = {n["Notification"]["Threshold"] for n in b["Properties"]["NotificationsWithSubscribers"]}
    assert {50, 80, 100} <= thresholds


def test_policy_least_privilege_details(tpl):
    (pol,) = _resources(tpl, "AWS::IAM::ManagedPolicy").values()
    actions = set()
    for stmt in pol["Properties"]["PolicyDocument"]["Statement"]:
        if "Fn::If" in stmt:
            stmt = stmt["Fn::If"][1]
        if stmt["Effect"] == "Allow":
            a = stmt["Action"]
            actions |= set(a if isinstance(a, list) else [a])
    assert "s3:DeleteObject" not in actions
    assert {"s3:GetBucketPublicAccessBlock", "s3:GetBucketLocation"} <= actions


def test_parameter_patterns_reject_wildcards(tpl):
    import re
    bed = re.compile(tpl["Parameters"]["BedrockModelArns"]["AllowedPattern"])
    assert bed.match("")
    assert bed.match("arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-lite-v1:0")
    assert not bed.match("*")
    assert not bed.match("arn:aws:bedrock:us-east-1::foundation-model/*")
    pr = re.compile(tpl["Parameters"]["TrustedPrincipalArn"]["AllowedPattern"])
    assert pr.match("arn:aws:iam::123456789012:role/sso-admin")
    assert not pr.match("arn:aws:iam::123456789012:role/*")


def test_lifecycle_rules(tpl):
    (bucket,) = _resources(tpl, "AWS::S3::Bucket").values()
    rules = {r["Id"]: r for r in bucket["Properties"]["LifecycleConfiguration"]["Rules"]}
    assert rules["expire-noncurrent-30d"]["ExpiredObjectDeleteMarker"] is True
    assert rules["expire-traces-90d"]["Prefix"] == "traces/"
