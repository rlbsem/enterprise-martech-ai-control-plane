"""Create a NEW PITR instance only after the cluster is closed. Never rebind or resume automatically."""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import boto3

from .release import Release


def restore_request(source, target, at):
    if not re.fullmatch(r'control-(dev|stage|prod)-restore-[a-z0-9-]{1,30}', target):
        raise ValueError('Use a distinct control-<environment>-restore-<incident> identifier')
    if target == source['DBInstanceIdentifier'] or source['Engine'] != 'postgres':
        raise ValueError('Restore must be an independent PostgreSQL instance')
    if not source['StorageEncrypted'] or source['PubliclyAccessible']:
        raise ValueError('Unexpected database security boundary')
    if at.tzinfo is None or not source['EarliestRestorableTime'] <= at <= source['LatestRestorableTime']:
        raise ValueError('Restore timestamp must be timezone-aware and inside the reported backup window')
    return {'SourceDBInstanceIdentifier': source['DBInstanceIdentifier'], 'TargetDBInstanceIdentifier': target,
            'RestoreTime': at, 'DBInstanceClass': source['DBInstanceClass'],
            'DBSubnetGroupName': source['DBSubnetGroup']['DBSubnetGroupName'],
            'VpcSecurityGroupIds': [g['VpcSecurityGroupId'] for g in source['VpcSecurityGroups']],
            'DBParameterGroupName': source['DBParameterGroups'][0]['DBParameterGroupName'],
            'MultiAZ': source['MultiAZ'], 'PubliclyAccessible': False, 'DeletionProtection': True,
            'CopyTagsToSnapshot': True, 'ManageMasterUserPassword': True,
            'MasterUserSecretKmsKeyId': source['MasterUserSecret']['KmsKeyId'],
            'EnableCloudwatchLogsExports': ['postgresql', 'upgrade'],
            'Tags': [{'Key': 'Application', 'Value': 'enterprise-control'}, {'Key': 'RecoverySource',
                     'Value': source['DBInstanceIdentifier']}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--at', required=True, help='ISO 8601 UTC timestamp inside RDS recovery window')
    parser.add_argument('--execute', action='store_true', help='Explicitly authorize creating the new RDS instance')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    session = boto3.Session(region_name=manifest['region'])
    release = Release(manifest, session.client('ecs'))
    release.assert_drained()
    rds = session.client('rds')
    source = rds.describe_db_instances(DBInstanceIdentifier=manifest['control_db'])['DBInstances'][0]
    request = restore_request(source, args.target, datetime.fromisoformat(args.at))
    if args.execute:
        rds.restore_db_instance_to_point_in_time(**request)
    print(json.dumps({'action': 'restore_requested' if args.execute else 'review_restore_request',
                      'request': request, 'resume': 'blocked_pending_binding_and_reconciliation'}, default=str))


if __name__ == '__main__':
    main()
