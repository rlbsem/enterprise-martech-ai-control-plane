"""Conservative credit planning, not a billing cap or a promise of Free-plan eligibility."""

from decimal import Decimal, ROUND_UP


def estimate():
    d = Decimal
    # October 27 Ohio Pricing.GetProducts observations; never subtract free allocations.
    lines = [
        ("RDS PostgreSQL t4g.micro", d(2) * 4, d("0.016"), "instance-hours", "GGD6FE59WWUNDKDC"),
        ("RDS gp3 storage", d(40) * 4 / 730, d("0.115"), "GB-month", "JQQGK6D789TWJF8B"),
        ("Retained/automated backups", d(100) * 48 / 730, d("0.095"), "GB-month", "HQRQUXJ6DAN3XS6X"),
        ("Four private endpoints", d(4) * 4, d("0.010"), "endpoint-hours", "P2Q7Y6KZKKFAHEVG"),
        ("Fargate CPU", d("0.5") * 4, d("0.04048"), "vCPU-hours", "UGSJ89BUWB7SMJ6A"),
        ("Fargate memory", d(1) * 4, d("0.004445"), "GB-hours", "HH5EE3ZFWSFKGKWM"),
        # Deliberately conservative whole-month allowances; not quoted per-experiment charges.
        ("Two customer keys", d(2), d(1), "whole-month allowance", None),
        ("Up to ten runtime/owner secrets", d(10), d("0.40"), "whole-month allowance", None),
        ("Logs, alarms and bounded API traffic", d(1), d(1), "allowance", None),
        ("Private image/evidence storage and requests", d(1), d("0.50"), "allowance", None),
        ("Data transfer/endpoint processing", d(1), d("0.25"), "allowance", None),
        ("Scheduled cleanup/checker and failure queue", d(1), d("0.50"), "allowance", None),
        ("Contingency", d(1), d(2), "allowance", None),
    ]
    rows = [
        {
            "resource": name,
            "quantity": str(quantity),
            "unit_usd": str(rate),
            "unit": unit,
            "pricing_sku": sku,
            "planned_usd": str((quantity * rate).quantize(d("0.0001"), rounding=ROUND_UP)),
        }
        for name, quantity, rate, unit, sku in lines
    ]
    total = sum(d(r["planned_usd"]) for r in rows)
    if total > 20:
        raise ValueError("Owner's planned-consumption authority exceeded")
    return {
        "schema": "recovery-cost-plan.v1",
        "currency": "USD",
        "region": "us-east-2",
        "maximum_concurrent_rds_instances": 2,
        "runtime_hours_including_cleanup_allowance": 4,
        "planned_usd": str(total),
        "authorized_planned_ceiling_usd": "20.00",
        "lines": rows,
        "conditions": [
            "FREE/ACTIVE and sufficient credits rechecked before apply",
            "No Paid upgrade, billing changes or out-of-pocket authorization",
            "Three-hour work deadline; one-hour cleanup allowance, never extended",
            "100 GB backup allowance assumes removal within 48 hours",
            "No NAT gateway, public IPv4, load balancer, CodeBuild or paid inference",
            "Alerts and this estimate are not a hard billing cap",
            "Delayed credit/usage reporting cannot establish billing clearance",
        ],
    }
