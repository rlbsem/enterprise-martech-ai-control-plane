"""Injected AWS observations, never a native deployment claim."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import Mock

import pytest

from controlplane.aws_acceptance import AcceptanceBlocked, Witness, pinned_manifest
from test_cloud_operations import manifest as base_manifest


@pytest.fixture
def witness():
    m = base_manifest()
    prefix = "arn:aws:ecs:ca-central-1:123456789012:"
    m.update(
        environment="stage",
        cluster=prefix + "cluster/control-stage",
        services={k: "control-stage-" + k for k in m["services"]},
        tasks={k: prefix + "task-definition/control-stage-" + k + ":1" for k in m["tasks"]},
        groups={k: "sg-" + format(i + 1, "017x") for i, k in enumerate(m["tasks"])},
        subnets=["subnet-" + "1" * 17, "subnet-" + "2" * 17],
        control_db="control-stage-control",
        downstream_db="control-stage-downstream",
        log_groups={"verifier": "/enterprise/control-stage/verifier"},
    )
    clients = {s: Mock() for s in ("sts", "ecs", "rds", "logs")}
    clients["sts"].get_caller_identity.return_value = {"Account": "123456789012"}
    services = []
    for name, value in m["services"].items():
        services.append(
            {
                "serviceName": value,
                "clusterArn": m["cluster"],
                "status": "ACTIVE",
                "desiredCount": 1,
                "runningCount": 1,
                "pendingCount": 0,
                "taskDefinition": m["tasks"][name],
                "networkConfiguration": {
                    "awsvpcConfiguration": {
                        "assignPublicIp": "DISABLED",
                        "subnets": m["subnets"],
                        "securityGroups": [m["groups"][name]],
                    }
                },
            }
        )
    clients["ecs"].describe_services.return_value = {"services": services}

    def definitions(taskDefinition):
        name = next(k for k, v in m["tasks"].items() if v == taskDefinition)
        return {
            "taskDefinition": {
                "taskRoleArn": f"arn:aws:iam::123456789012:role/control-stage-{name}",
                "networkMode": "awsvpc",
                "requiresCompatibilities": ["FARGATE"],
                "containerDefinitions": [
                    {
                        "image": m["image"],
                        "command": ["python", "-m", "controlplane.cloud", name],
                        "environment": [{"name": "SOURCE_COMMIT", "value": m["commit"]}],
                    }
                ],
            }
        }

    clients["ecs"].describe_task_definition.side_effect = definitions
    clients["ecs"].list_tasks.side_effect = lambda **kw: {"taskArns": [prefix + "task/" + kw["serviceName"]]}

    def tasks(**kw):
        arn = kw["tasks"][0]
        name = arn.rsplit("/", 1)[-1].removeprefix("control-stage-")
        return {
            "tasks": [
                {
                    "taskArn": arn,
                    "lastStatus": "RUNNING",
                    "taskDefinitionArn": m["tasks"][name],
                    "containers": [{"imageDigest": "sha256:" + "a" * 64}],
                }
            ]
        }

    clients["ecs"].describe_tasks.side_effect = tasks

    def database(DBInstanceIdentifier):
        return {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": DBInstanceIdentifier,
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                    "EngineVersion": "17.6",
                    "PubliclyAccessible": False,
                    "StorageEncrypted": True,
                    "MultiAZ": True,
                    "DeletionProtection": True,
                    "BackupRetentionPeriod": 14,
                    "MasterUserSecret": {"SecretArn": "private-master-secret"},
                    "DbiResourceId": DBInstanceIdentifier,
                }
            ]
        }

    clients["rds"].describe_db_instances.side_effect = database
    return Witness(m, clients), m, clients


def test_readonly_runtime_and_independent_database_configuration(witness):
    w, m, clients = witness
    assert w.identity()["account_pin_verified"]
    assert w.runtime()["running_tasks_checked"] == 4
    assert w.databases()["independent_databases"] == 2
    assert len(w.calls) < 40
    assert all(c["status"] == "succeeded" for c in w.calls)
    assert "123456789012" not in json.dumps(w.calls)
    with pytest.raises(AcceptanceBlocked):
        w.call("ecs", "update_service")
    clients["ecs"].update_service.assert_not_called()


def test_cross_account_and_production_are_rejected(witness):
    w, m, clients = witness
    clients["sts"].get_caller_identity.return_value = {"Account": "other"}
    with pytest.raises(AcceptanceBlocked, match="account"):
        w.identity()
    m["environment"] = "prod"
    with pytest.raises(AcceptanceBlocked, match="Isolated"):
        pinned_manifest(m)


@pytest.mark.parametrize(
    "fault", ["revision", "counts", "public_ip", "subnets", "role_reuse", "running_image", "entrypoint"]
)
def test_configuration_drift_cannot_be_a_native_witness_pass(witness, fault):
    w, m, clients = witness
    first = clients["ecs"].describe_services.return_value["services"][0]
    if fault == "revision":
        first["taskDefinition"] = "other"
    elif fault == "counts":
        first["pendingCount"] = 1
    elif fault == "public_ip":
        first["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] = "ENABLED"
    elif fault == "subnets":
        first["networkConfiguration"]["awsvpcConfiguration"]["subnets"] = []
    elif fault in ("role_reuse", "entrypoint"):
        original = clients["ecs"].describe_task_definition.side_effect

        def changed(**kw):
            result = original(**kw)
            if fault == "role_reuse":
                result["taskDefinition"]["taskRoleArn"] = "arn:aws:iam::123456789012:role/reused"
            else:
                result["taskDefinition"]["containerDefinitions"][0]["entryPoint"] = ["sh", "-c"]
            return result

        clients["ecs"].describe_task_definition.side_effect = changed
    else:
        original = clients["ecs"].describe_tasks.side_effect

        def changed(**kw):
            result = original(**kw)
            result["tasks"][0]["containers"][0]["imageDigest"] = "sha256:" + "b" * 64
            return result

        clients["ecs"].describe_tasks.side_effect = changed
    with pytest.raises(AcceptanceBlocked):
        w.runtime()


@pytest.mark.parametrize(
    "field,value",
    [
        ("DbiResourceId", "same"),
        ("PubliclyAccessible", True),
        ("StorageEncrypted", False),
        ("MultiAZ", False),
        ("BackupRetentionPeriod", 1),
        ("MasterUserSecret", {}),
    ],
)
def test_managed_database_security_and_independence_are_observed(witness, field, value):
    w, m, clients = witness
    original = clients["rds"].describe_db_instances.side_effect

    def changed(**kw):
        result = original(**kw)
        result["DBInstances"][0][field] = value
        return result

    clients["rds"].describe_db_instances.side_effect = changed
    with pytest.raises(AcceptanceBlocked):
        w.databases()


def verifier(w, m, clients):
    for service in clients["ecs"].describe_services.return_value["services"]:
        service.update(desiredCount=0, runningCount=0)
    clients["ecs"].list_tasks.side_effect = None
    clients["ecs"].list_tasks.return_value = {"taskArns": []}
    w.runtime(quiesced=True)
    arn = "arn:aws:ecs:ca-central-1:123456789012:task/control-stage/verifier-observation"
    clients["ecs"].describe_tasks.side_effect = None
    clients["ecs"].describe_tasks.return_value = {
        "tasks": [
            {
                "taskArn": arn,
                "taskDefinitionArn": m["tasks"]["verifier"],
                "lastStatus": "STOPPED",
                "containers": [{"exitCode": 0, "imageDigest": "sha256:" + "a" * 64}],
            }
        ]
    }
    proof = {
        "safe_to_resume": True,
        "findings": [],
        "local_intents": 2,
        "remote_effects": 1,
        "snapshot_hash": "d" * 64,
        "observed_at": datetime.now(UTC).isoformat(),
    }
    clients["logs"].get_log_events.return_value = {"events": [{"message": json.dumps(proof)}]}
    return arn, proof


def test_actual_task_log_identity_is_required_and_never_authorizes_resume(witness):
    w, m, clients = witness
    arn, _ = verifier(w, m, clients)
    result = w.recovery(arn)
    assert result["safe_to_resume_at_observation"]
    assert result["authorization"] == "read_only_observation_never_permission_to_resume"
    assert "123456789012" not in json.dumps(result)
    assert (
        clients["logs"].get_log_events.call_args.kwargs["logStreamName"]
        == "task/verifier/verifier-observation"
    )


@pytest.mark.parametrize(
    "fault", ["old", "future", "finding", "missing", "multiple", "wrong_image", "not_quiesced"]
)
def test_saved_or_failed_or_unbound_verifier_cannot_claim_recovery(witness, fault):
    w, m, clients = witness
    arn, proof = verifier(w, m, clients)
    if fault == "old":
        proof["observed_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    elif fault == "future":
        proof["observed_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    elif fault == "finding":
        proof["findings"] = [{"reason": "remote_ahead_missing_intent"}]
    elif fault == "wrong_image":
        clients["ecs"].describe_tasks.return_value["tasks"][0]["containers"][0]["imageDigest"] = "latest"
    elif fault == "not_quiesced":
        w.quiescence_verified = False
    events = [{"message": json.dumps(proof)}]
    if fault == "missing":
        events = []
    if fault == "multiple":
        events *= 2
    clients["logs"].get_log_events.return_value = {"events": events}
    with pytest.raises(AcceptanceBlocked):
        w.recovery(arn)


def test_manifest_network_and_database_pins_are_closed(witness):
    _, m, _ = witness
    for changed in [
        dict(m, subnets=["subnet-" + "1" * 17] * 2),
        dict(m, control_db="other"),
        dict(m, cluster="other"),
    ]:
        with pytest.raises(AcceptanceBlocked):
            pinned_manifest(deepcopy(changed))
