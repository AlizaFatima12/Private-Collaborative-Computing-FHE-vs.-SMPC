"""Unit and integration tests for MP-SPDZ 3-Party Secret Sharing and Beaver Triple Multiplication."""

import pytest
from dataset import DatasetGenerator
from smpc import (
    SMPCProtocolConfig,
    SecretSharingInspector,
    BeaverTripleAnalyzer,
    SMPCBenchmarkEngine,
)


def test_smpc_dishonest_majority_aggregation():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = SMPCBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="aggregation",
        protocol_key="semi_dishonest_majority_3p",
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.total_sum
    assert abs(res["computed_average"] - ds.reference.exact_average) < 1e-9
    assert res["beaver_triples_consumed"] == 0
    assert res["protocol_network_bytes"] > 0


def test_smpc_beaver_triple_risk_multiplication():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = SMPCBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="risk_multiplication",
        protocol_key="semi_dishonest_majority_3p",
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.risk_total
    assert res["beaver_triples_consumed"] == ds.total_transactions
    assert res["smpc_triple_reuse_prevented"] is True
    assert res["preprocessing_time_s"] > 0.0
    assert res["online_time_s"] > 0.0
    assert res["communication_rounds"] >= 10


def test_smpc_shamir_honest_majority():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = SMPCBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="risk_multiplication",
        protocol_key="shamir_honest_majority_3p",
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.risk_total
    assert "Honest majority" in res["trust_assumption"]


def test_smpc_storage_expansion_calculation():
    ds = DatasetGenerator().generate("medium", seed=42)
    cfg = SMPCProtocolConfig()
    spec_semi = cfg.get_protocol_spec("semi_dishonest_majority_3p")
    metrics = SecretSharingInspector.compute_storage_metrics(
        ds, spec_semi, workload="risk_multiplication"
    )
    # 1000 transactions * 2 vectors * 8 bytes = 16,000 plaintext bytes
    assert metrics.plaintext_bytes_total == 16000
    # 3 parties * 16-byte field share * 2000 elements = 96,000 share bytes (6.0x expansion)
    assert metrics.total_share_bytes_all_parties == 96000
    assert abs(metrics.share_expansion_ratio_all_parties - 6.0) < 1e-9


def test_beaver_triple_reuse_forbidden():
    with pytest.raises(RuntimeError, match="SECURITY VIOLATION"):
        BeaverTripleAnalyzer.analyze_execution_logs(
            combined_logs="PCC_TRIPLES_CONSUMED=100",
            workload="risk_multiplication",
            num_transactions=100,
            purged_before_run=False,
        )
