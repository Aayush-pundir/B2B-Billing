"""Golden cases from the billing specification (§12), with anonymised names.
Each asserts the engine reproduces the legacy-sheet taxable value exactly."""
import datetime
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.engine2 import compute_account  # noqa: E402

CTX = {"period_start": datetime.date(2026, 9, 1), "days_in_month": 30, "active_days": 30, "active_fraction": D(1), "gst_rate": D("0.18")}


def run(components, usage, ctx=None, **kw):
    return compute_account(components, usage, {**CTX, **(ctx or {})}, **kw)["subtotal"]


def test_fixed_plus_upi_with_escalation():                      # case 1
    assert run([{"component_type": "fixed_saas", "params": {"base_amount": 15000, "escalations_applied": [1.10]}},
                {"component_type": "upi_per_txn", "params": {"rate": 0.75, "free_txns": 0}}], {"upi_txn_count": 301}) == D("16725.75")


def test_fixed_upi_free():                                       # case 2
    assert run([{"component_type": "fixed_saas", "params": {"base_amount": 15000, "escalations_applied": [1.10]}},
                {"component_type": "upi_free"}], {"upi_txn_count": 1444}) == D("16500.00")


def test_load_percent_with_min():                                # case 3
    assert run([{"component_type": "load_percent", "params": {"rate": 0.008, "min_amount": 30000}}], {"add_money": 3709745}) == D("30000.00")


def test_fixed_upi_free_quota_admin_free():                      # case 4
    comps = [{"component_type": "fixed_saas", "params": {"base_amount": 7500}},
             {"component_type": "upi_per_txn", "params": {"rate": 1.5, "free_txns": 600}},
             {"component_type": "per_admin", "params": {"rate": 500, "free_admins": 2}}]
    assert run(comps, {"upi_txn_count": 2884, "admin_count": 2, "maker_count": 0}) == D("10926.00")


def test_upi_only():                                             # case 5
    assert run([{"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}}], {"upi_txn_count": 456}) == D("1140.00")


def test_fixed_minus_upi_credit():                               # case 6
    assert run([{"component_type": "fixed_minus_upi_credit", "params": {"base": 10000, "credit_per_upi_txn": 0.5}}], {"upi_txn_count": 13732}) == D("3134.00")


def test_fixed_minus_upi_credit_never_negative():
    assert run([{"component_type": "fixed_minus_upi_credit", "params": {"base": 10000, "credit_per_upi_txn": 0.5}}], {"upi_txn_count": 50000}) == D("0.00")


def test_standard_template_all_components():                     # case 8
    comps = [{"component_type": "load_percent", "params": {"rate": 0.003}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}},
             {"component_type": "per_user", "params": {"rate": 200, "min_users": 5}}, {"component_type": "per_admin", "params": {"rate": 1000}}]
    assert run(comps, {"add_money": 0, "upi_txn_count": 1, "maker_count": 0, "admin_count": 1}) == D("2002.50")


def test_spend_percent_after_free_txns():                        # case 9
    comps = [{"component_type": "fixed_amount", "params": {"amount": 5000}}, {"component_type": "spend_percent_after_free_txns", "params": {"free_txns": 1000, "pct": 1}}]
    assert run(comps, {"upi_spend_after_free": 2085668}) == D("25856.68")


def test_overall_minimum_floor():                                # case 10
    comps = [{"component_type": "load_percent", "params": {"rate": 0.0035}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}},
             {"component_type": "per_user", "params": {"rate": 199, "min_users": 5}}, {"component_type": "per_admin", "params": {"rate": 1000}}]
    res = compute_account(comps, {"add_money": 182693, "upi_txn_count": 85, "maker_count": 2, "admin_count": 1}, CTX, overall_min=4000)
    assert res["subtotal"] == D("4000.00") and any(l["component_type"] == "mg_topup" for l in res["lines"])


def test_gst_inclusive_fixed():                                  # case 11
    assert run([{"component_type": "fixed_saas_gst_inclusive", "params": {"gross_amount": 25000}}], {}) == D("21186.44")


def test_load_percent_plus_fixed():                              # case 12
    comps = [{"component_type": "load_percent_plus_fixed", "params": {"rate": 0.002, "fixed": 3500}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}}]
    assert run(comps, {"add_money": 3888207, "upi_txn_count": 2349}) == D("17148.91")


def test_users_admin_and_floor():                                # case 13
    comps = [{"component_type": "load_percent", "params": {"rate": 0.002}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}},
             {"component_type": "per_user", "params": {"rate": 150, "min_users": 0}}, {"component_type": "per_admin", "params": {"rate": 1000}}]
    assert run(comps, {"add_money": 1392512.44, "upi_txn_count": 585, "maker_count": 26, "admin_count": 2}, overall_min=20000) == D("20000.00")


def test_pro_rata_load_fee_but_not_minimum():                    # case 14
    comps = [{"component_type": "load_percent", "params": {"rate": 0.0025}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}},
             {"component_type": "per_user", "params": {"rate": 200, "min_users": 0}}, {"component_type": "per_admin", "params": {"rate": 1000}}]
    ctx = {"active_days": 15, "days_in_month": 31, "active_fraction": D(15) / D(31)}
    usage = {"add_money": 1000846, "upi_txn_count": 417, "maker_count": 18, "admin_count": 1}
    assert run(comps, usage, ctx) == D("6853.20")
    assert run(comps, usage, ctx, overall_min=10000) == D("10000.00")


def test_missing_usage_is_zero_but_commitments_still_bill():
    comps = [{"component_type": "fixed_saas", "params": {"base_amount": 5000}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}}]
    assert run(comps, {}) == D("5000.00")


def test_cap_applies():
    assert run([{"component_type": "fixed_saas", "params": {"base_amount": 20000}}], {}, overall_cap=15000) == D("15000.00")


def test_mg_deferred_until_effective_date():
    comp = [{"component_type": "load_percent", "params": {"rate": 0.01}}]
    assert run(comp, {"add_money": 100000}, overall_min=5000, mg_effective_from=datetime.date(2026, 10, 1)) == D("1000.00")
    assert run(comp, {"add_money": 100000}, {"period_start": datetime.date(2026, 10, 1)}, overall_min=5000, mg_effective_from=datetime.date(2026, 10, 1)) == D("5000.00")


def test_subscription_covers_platform_fee_but_not_usage():
    comps = [{"component_type": "fixed_saas", "params": {"base_amount": 55000}}, {"component_type": "upi_per_txn", "params": {"rate": 2.5, "free_txns": 0}}]
    assert run(comps, {"upi_txn_count": 456}, covered_groups={"A"}) == D("1140.00")


def test_tiered_whole_vs_marginal():
    tiers = [{"upto": 1000000, "rate": 0.02, "min": 0}, {"upto": 1500000, "rate": 0.018, "min": 0}, {"upto": None, "rate": 0.015, "min": 0}]
    whole = run([{"component_type": "load_percent_tiered", "params": {"tiers": tiers, "mode": "whole"}}], {"add_money": 1600000})
    marginal = run([{"component_type": "load_percent_tiered", "params": {"tiers": tiers, "mode": "marginal"}}], {"add_money": 1600000})
    assert whole == D("24000.00")                         # 1.5% × 16L applied to everything
    assert marginal == D("30500.00")                      # 2%×10L + 1.8%×5L + 1.5%×1L
