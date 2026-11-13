"""Fail-closed ECS release/restore gate. No infrastructure is provisioned by this tool."""

import argparse
import json
import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import boto3


def validate_manifest(manifest):
    if not re.fullmatch(r'[a-f0-9]{40}', manifest['commit']):
        raise ValueError('Invalid source commit')
    if not re.fullmatch(r'[0-9]{12}\.dkr\.ecr\.ca-central-1\.amazonaws\.com/[a-z0-9-]+@sha256:[a-f0-9]{64}',
                        manifest['image']):
        raise ValueError('A digest-qualified private ECR image is required')
    expected = {'api', 'remote', 'worker', 'observer'}
    if set(manifest['services']) != expected or set(manifest['replicas']) != expected:
        raise ValueError('Unexpected service topology')
    if set(manifest['tasks']) != expected | {'migration', 'verifier'}:
        raise ValueError('Unexpected task topology')
    if any(type(n) is not int or n < 1 or n > 8 for n in manifest['replicas'].values()):
        raise ValueError('Replica counts must be bounded 1..8')
    if manifest['region'] != 'ca-central-1':
        raise ValueError('Unexpected region')
    return manifest


class Release:
    def __init__(self, manifest, ecs):
        self.m = validate_manifest(manifest)
        self.ecs = ecs

    def validate_tasks(self):
        for name, arn in self.m['tasks'].items():
            task = self.ecs.describe_task_definition(taskDefinition=arn)['taskDefinition']
            containers = task['containerDefinitions']
            if len(containers) != 1 or containers[0]['image'] != self.m['image']:
                raise RuntimeError('Task definition image does not match approved release')
            env = {v['name']: v['value'] for v in containers[0]['environment']}
            if env.get('SOURCE_COMMIT') != self.m['commit']:
                raise RuntimeError('Task definition source provenance mismatch')
            if containers[0]['command'] != ['python', '-m', 'controlplane.cloud', name]:
                raise RuntimeError('Unexpected task entrypoint')
            container = containers[0]
            if container.get('entryPoint'):
                raise RuntimeError('Task entrypoint overrides are not approved')
            forbidden = {'CONTROL_ADMIN_DSN', 'TEST_ADMIN_DSN', 'CONTROL_DSN', 'REMOTE_DSN',
                         'API_TOKENS', 'EXECUTOR_TOKEN', 'INJECTOR_TOKEN',
                         'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN'}
            if forbidden.intersection(env) or container.get('secrets'):
                raise RuntimeError('Runtime credentials must use the scoped startup secret')
            if len(env) != len(container['environment']):
                raise RuntimeError('Duplicate task environment names')
            if task.get('status', 'ACTIVE') != 'ACTIVE':
                raise RuntimeError('Inactive task revision')

    def services(self):
        result = self.ecs.describe_services(cluster=self.m['cluster'], services=list(self.m['services'].values()))
        if result.get('failures') or len(result['services']) != len(self.m['services']):
            raise RuntimeError('Cannot establish service state')
        return result['services']

    def assert_drained(self):
        if any(s['desiredCount'] or s['runningCount'] or s['pendingCount'] for s in self.services()):
            raise RuntimeError('All services must be drained before comparing recovery state')
        for page in self.ecs.get_paginator('list_tasks').paginate(cluster=self.m['cluster'], desiredStatus='RUNNING'):
            if page['taskArns']:
                raise RuntimeError('One-off tasks still running; recovery boundary is not quiescent')

    def quiesce(self):
        # Admission first, then workers. Durable intent and leases survive shutdown.
        for name in ('api', 'worker', 'remote', 'observer'):
            self.ecs.update_service(cluster=self.m['cluster'], service=self.m['services'][name], desiredCount=0)
        self.ecs.get_waiter('services_stable').wait(cluster=self.m['cluster'],
            services=list(self.m['services'].values()), WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
        self.assert_drained()

    def utility(self, name):
        result = self.ecs.run_task(cluster=self.m['cluster'], taskDefinition=self.m['tasks'][name],
            launchType='FARGATE', platformVersion='1.4.0', count=1,
            networkConfiguration={'awsvpcConfiguration': {'subnets': self.m['subnets'],
                'securityGroups': [self.m['groups'][name]], 'assignPublicIp': 'DISABLED'}})
        if result.get('failures') or len(result.get('tasks', [])) != 1:
            for task in result.get('tasks', []):
                self.ecs.stop_task(cluster=self.m['cluster'], task=task['taskArn'],
                                   reason='Partial utility launch rejected')
            raise RuntimeError('Utility task could not start')
        arn = result['tasks'][0]['taskArn']
        try:
            self.ecs.get_waiter('tasks_stopped').wait(cluster=self.m['cluster'], tasks=[arn],
                                                   WaiterConfig={'Delay': 10, 'MaxAttempts': 90})
        except Exception:
            # A timed-out waiter does not stop a billable utility. Stop only the task
            # this call launched; never sweep or terminate another operator's tasks.
            try:
                self.ecs.stop_task(cluster=self.m['cluster'], task=arn, reason='Bounded utility wait failed')
            except Exception:
                raise RuntimeError('Utility wait and stop failed; explicit cleanup required') from None
            raise RuntimeError('Utility wait failed; stop requested, termination must be confirmed') from None
        response = self.ecs.describe_tasks(cluster=self.m['cluster'], tasks=[arn])
        if response.get('failures') or len(response.get('tasks', [])) != 1:
            raise RuntimeError('Utility final state could not be established')
        task = response['tasks'][0]
        if (task.get('taskArn') != arn or task.get('lastStatus') != 'STOPPED'
                or task.get('taskDefinitionArn') != self.m['tasks'][name]):
            raise RuntimeError('Utility stopped task identity mismatch')
        containers = task.get('containers', [])
        if len(containers) != 1 or containers[0].get('exitCode') != 0:
            raise RuntimeError('Utility failed; services remain closed. Review its bounded CloudWatch findings.')
        return arn

    def resume(self):
        self.validate_tasks()
        self.assert_drained()
        proof_task = self.utility('verifier')
        self.assert_drained()
        # A fresh verifier runs each time: a saved report can never authorize a later replay.
        try:
            for name in ('remote', 'api', 'worker', 'observer'):
                self.ecs.update_service(cluster=self.m['cluster'], service=self.m['services'][name],
                    taskDefinition=self.m['tasks'][name], desiredCount=self.m['replicas'][name])
                self.ecs.get_waiter('services_stable').wait(cluster=self.m['cluster'],
                    services=[self.m['services'][name]], WaiterConfig={'Delay': 10, 'MaxAttempts': 60})
                # ECS circuit-breaker rollback may also report stability. Verify the requested revision won.
                current = next(s for s in self.services() if s['serviceName'] == self.m['services'][name])
                if current['taskDefinition'] != self.m['tasks'][name]:
                    raise RuntimeError('Deployment rolled back instead of accepting requested revision')
        except Exception:
            self.quiesce()
            raise
        return proof_task


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['deploy', 'quiesce', 'verify', 'resume', 'rollback'])
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--record', type=Path, required=True)
    args = parser.parse_args()
    if args.record.exists():
        parser.error('Choose a fresh record path; execution records are not overwritten')
    record = {'action': args.action, 'status': 'FAILED', 'started_at': datetime.now(UTC).isoformat(),
              'scope': 'AWS operational command; native success requires independently checked evidence'}
    try:
        manifest = json.loads(args.manifest.read_text())
        release = Release(manifest, boto3.client('ecs', region_name=manifest['region']))
        record.update(commit=manifest['commit'], image_sha256=manifest['image'].rsplit('@', 1)[-1],
                      cluster_reference=reference(manifest['cluster']))
        release.validate_tasks()
        proof = None
        if args.action in ('deploy', 'rollback', 'quiesce'):
            release.quiesce()
        if args.action == 'deploy':
            release.utility('migration')
        if args.action in ('deploy', 'resume', 'rollback'):
            proof = release.resume()
        elif args.action == 'verify':
            release.assert_drained()
            proof = release.utility('verifier')
        record.update(status='COMPLETED', proof_task_reference=reference(proof) if proof else None,
                      task_references={k: reference(v) for k, v in manifest['tasks'].items()})
    except Exception as exc:
        record['error_type'] = type(exc).__name__
        record['operator_action'] = 'Inspect private AWS state; success and cleanup are not established'
    record['completed_at'] = datetime.now(UTC).isoformat()
    args.record.parent.mkdir(parents=True, exist_ok=True)
    args.record.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps(record))
    return 0 if record['status'] == 'COMPLETED' else 1


def reference(value):
    return hashlib.sha256(value.encode()).hexdigest()


if __name__ == '__main__':
    raise SystemExit(main())
