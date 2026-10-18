"""No AWS calls: reject unsafe or misleading acceptance cost assumptions."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ohio_estimate', ROOT / 'scripts/estimate_ohio_acceptance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def rates():
    return json.loads(MODULE.RATES.read_text())


def test_bound_includes_independent_store_restore_and_cleanup():
    result = MODULE.estimate(rates())
    assert result['items']['three_single_az_micro_instances'] == '0.192000'
    assert result['items']['four_interface_endpoint_az_units'] == '0.160000'
    assert result['items']['sixty_codebuild_cleanup_and_image_build_minutes'] == '0.300000'
    assert result['planning_allowance'] == '5.00'
    assert result['actual_usage'] == 'not_executed'
    assert not result['paid_plan_authorized']


@pytest.mark.parametrize('hours', [0, 5, True, 1.5])
def test_no_open_ended_acceptance_window(hours):
    with pytest.raises(ValueError):
        MODULE.estimate(rates(), hours)


@pytest.mark.parametrize('value', ['0', '-1', 'NaN', 'Infinity', '100'])
def test_no_free_discount_or_unbounded_cost(value):
    document = deepcopy(rates())
    document['rates']['rds_instance_hour']['usd'] = value
    with pytest.raises(ValueError):
        MODULE.estimate(document)


def test_production_region_cannot_silently_reuse_ohio_prices():
    document = rates()
    document['region'] = 'ca-central-1'
    with pytest.raises(ValueError):
        MODULE.estimate(document)
