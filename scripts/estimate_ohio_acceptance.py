"""Reproduce a bounded Ohio acceptance estimate from checked-in public AWS rates.

This calculator performs no AWS calls, grants no provisioning authority and never
deducts assumed Free Tier usage. Amounts describe modeled credit consumption.
"""

import argparse
from decimal import Decimal
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RATES = ROOT / 'docs/evidence/aws-readiness/ohio/public-rates.json'


def estimate(document, hours=4):
    if type(hours) is not int or not 1 <= hours <= 4:
        raise ValueError('Acceptance window must be one to four whole hours')
    if document['region'] != 'us-east-2':
        raise ValueError('Only the Ohio acceptance profile is priced')
    rates = {key: Decimal(value['usd']) for key, value in document['rates'].items()}
    if any(not value.is_finite() or value <= 0 for value in rates.values()):
        raise ValueError('Finite positive on-demand rates required; no assumed free discounts')
    h, month = Decimal(hours), Decimal(730)
    # Include the third PITR instance for the entire window, conservatively.
    items = {
        'three_single_az_micro_instances': 3 * h * rates['rds_instance_hour'],
        'sixty_gb_gp3': 60 * h / month * rates['rds_gp3_gb_month'],
        'sixty_gb_additional_backup_allowance': 60 * h / month * rates['rds_backup_gb_month'],
        'four_interface_endpoint_az_units': 4 * h * rates['endpoint_az_hour'],
        'eight_aggregate_quarter_cpu_half_gb_task_hours': 8 * (
            Decimal('0.25') * rates['fargate_vcpu_hour'] + Decimal('0.5') * rates['fargate_gb_hour']),
        'eight_secrets': 8 * h / month * rates['secret_month'],
        'two_kms_keys': 2 * h / month * rates['kms_key_month'],
        'sixty_codebuild_cleanup_and_image_build_minutes': 60 * rates['codebuild_minute'],
        'logs_allowance_not_metered': Decimal('0.50'),
        'storage_transfer_and_request_allowance_not_metered': Decimal('0.25'),
        'scheduler_and_independent_check_allowance_not_metered': Decimal('0.10'),
        'residual_retention_allowance_not_metered': Decimal('0.25'),
    }
    total = sum(items.values())
    allowance = Decimal('5.00')
    if total > allowance:
        raise ValueError('Modeled profile exceeds the five-credit planning allowance')
    return {'profile': 'ohio-free-single-az-pitr-v1', 'region': 'us-east-2',
            'status': 'PROPOSED_NOT_AUTHORIZED_NOT_DEPLOYED', 'hours': hours,
            'currency': 'USD_equivalent_AWS_credit_consumption',
            'items': {key: str(value.quantize(Decimal('0.000001'))) for key, value in items.items()},
            'modeled_total': str(total.quantize(Decimal('0.000001'))),
            'planning_allowance': str(allowance),
            'contingency': str((allowance - total).quantize(Decimal('0.000001'))),
            'hard_billing_cap': False, 'actual_usage': 'not_executed',
            'paid_plan_authorized': False, 'out_of_pocket_authorized': False,
            'full_application_acceptance': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rates', type=Path, default=RATES)
    parser.add_argument('--hours', type=int, default=4)
    args = parser.parse_args()
    print(json.dumps(estimate(json.loads(args.rates.read_text()), args.hours), indent=2))


if __name__ == '__main__':
    main()
