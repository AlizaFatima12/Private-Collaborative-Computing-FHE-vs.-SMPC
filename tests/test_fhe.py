"""Unit and integration tests for OpenFHE 3-Party Threshold BFV, Single-Key Baseline, and Noise Analysis."""

from dataset import DatasetGenerator
from fhe import (
    FHEContextManager,
    FHEKeyGenerator,
    FHEEncryptor,
    FHEEvaluator,
    FHEDecryptor,
    FHENoiseAnalyzer,
    FHEBenchmarkEngine,
)


def test_fhe_threshold_3party_aggregation():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = FHEBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="aggregation",
        profile_name="threshold_bfv_128",
        enable_network_transport=True,
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.total_sum
    assert abs(res["computed_average"] - ds.reference.exact_average) < 1e-9
    assert res["ciphertext_size_bytes"] > res["plaintext_size_bytes"]
    assert res["expansion_ratio"] > 1.0
    assert res["fhe_num_parties"] == 3
    assert res["fhe_ring_dimension"] >= 4096


def test_fhe_threshold_3party_risk_multiplication():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = FHEBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="risk_multiplication",
        profile_name="threshold_bfv_128",
        enable_network_transport=True,
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.risk_total
    assert res["multiplication_time_s"] > 0.0
    assert res["fhe_eval_mult_key_size_bytes"] > 0
    assert res["fhe_num_ciphertext_polynomials"] == 2
    assert res["fhe_estimated_remaining_noise_budget_bits"] > 50.0


def test_fhe_single_key_baseline_separated_trust():
    ds = DatasetGenerator().generate("small", seed=42)
    engine = FHEBenchmarkEngine()
    res = engine.run_pipeline(
        dataset=ds,
        workload="risk_multiplication",
        profile_name="single_key_bfv_128",
        enable_network_transport=True,
    )
    assert res["correctness_verified"] is True
    assert res["computed_value"] == ds.reference.risk_total
    assert res["security_model"] == "single_key_baseline"
    assert "NOT equivalent to threshold" in res["trust_assumption"]


def test_fhe_isolated_bootstrapping_benchmark():
    boot_res = FHENoiseAnalyzer.run_isolated_bootstrapping_benchmark()
    assert boot_res["profile"] == "bootstrapping_ckks_exp"
    assert boot_res["bootstrap_keygen_time_s"] > 0.0
    assert boot_res["bootstrap_eval_time_s"] > 0.0
    assert (
        boot_res["levels_remaining_after_bootstrap"]
        > boot_res["levels_remaining_before_bootstrap"]
    )
    assert boot_res["max_approximation_error"] < 0.05
