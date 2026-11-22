"""Unit tests for statistical aggregation, crossover threshold detection, and graph generation."""

from pathlib import Path
from orchestrator.metrics_collector import MetricsCollector
from analysis.statistics import StatisticalComparator
from analysis.crossover_analysis import CrossoverAnalyzer
from analysis.graphs import AcademicGraphGenerator


def test_statistical_comparator_significance_and_speedup():
    fhe_trials = [0.25, 0.26, 0.24, 0.25, 0.25]
    smpc_trials = [0.50, 0.52, 0.49, 0.51, 0.50]
    res = StatisticalComparator.compare_trials(fhe_trials, smpc_trials)

    assert res["faster_technology"] == "FHE"
    assert res["speedup_factor"] >= 1.9
    assert res["statistically_significant"] is True
    assert res["delta_fhe_minus_smpc_mean_s"] < 0


def test_metrics_collector_and_crossover_pipeline(tmp_path: Path):
    collector = MetricsCollector(results_dir=tmp_path)

    # Create synthetic multi-repetition records exhibiting a latency crossover between 10ms and 50ms
    latencies = [("local", 1.0), ("fast_wan", 10.0), ("moderate_wan", 50.0)]
    for lat_name, lat_ms in latencies:
        for rep in range(1, 4):
            fhe_time = 0.30 + 0.001 * lat_ms + 0.002 * rep
            smpc_time = 0.12 + 0.008 * lat_ms + 0.002 * rep

            base_meta = {
                "workload": "risk_multiplication",
                "dataset_profile": "medium",
                "num_transactions": 1000,
                "num_participants": 3,
                "latency_profile": lat_name,
                "configured_latency_ms": lat_ms,
                "expected_rtt_ms": lat_ms * 2.0,
                "measured_rtt_ms": lat_ms * 2.0,
                "bandwidth_profile": "high",
                "configured_bandwidth_mbps": 1000.0,
                "configured_packet_loss_pct": 0.0,
                "plaintext_size_bytes": 16000,
                "correctness_verified": True,
                "trust_comparison_label": "DIRECTLY_EQUIVALENT",
            }

            fhe_rec = {
                **base_meta,
                "technology": "FHE",
                "protocol": "threshold_bfv_128",
                "scheme": "BFVRNS",
                "security_model": "threshold_multiparty",
                "trust_assumption": "Dishonest majority",
                "total_execution_time_s": fhe_time,
                "setup_time_s": 0.15,
                "preprocessing_time_s": 0.0,
                "input_protect_time_s": 0.08,
                "addition_time_s": 0.01,
                "multiplication_time_s": 0.04,
                "computation_time_s": 0.05,
                "output_recover_time_s": 0.02,
                "online_time_s": fhe_time - 0.15,
                "measured_network_traffic_bytes": 4500000,
                "measured_network_traffic_mb": 4.29,
                "protocol_network_bytes": 4500000,
                "crypto_payload_size_bytes": 1500000,
                "expansion_ratio": 93.75,
                "storage_overhead_bytes": 4500000,
                "communication_rounds": 7,
                "beaver_triples_consumed": 0,
                "peak_memory_mb": 210.0,
            }
            smpc_rec = {
                **base_meta,
                "technology": "SMPC",
                "protocol": "semi_dishonest_majority_3p",
                "scheme": "Additive Secret Sharing",
                "security_model": "Semi-honest",
                "trust_assumption": "Dishonest majority",
                "total_execution_time_s": smpc_time,
                "setup_time_s": 0.03,
                "preprocessing_time_s": smpc_time * 0.65,
                "input_protect_time_s": smpc_time * 0.10,
                "addition_time_s": smpc_time * 0.02,
                "multiplication_time_s": smpc_time * 0.13,
                "computation_time_s": smpc_time * 0.15,
                "output_recover_time_s": smpc_time * 0.05,
                "online_time_s": smpc_time * 0.30,
                "measured_network_traffic_bytes": 620000,
                "measured_network_traffic_mb": 0.59,
                "protocol_network_bytes": 620000,
                "crypto_payload_size_bytes": 96000,
                "expansion_ratio": 6.0,
                "storage_overhead_bytes": 96000,
                "communication_rounds": 26,
                "beaver_triples_consumed": 1000,
                "peak_memory_mb": 65.0,
            }
            collector.add_trial_record(fhe_rec, "latency_sweep", rep, is_warmup=False)
            collector.add_trial_record(smpc_rec, "latency_sweep", rep, is_warmup=False)

    paths = collector.export_all()
    assert paths["raw_csv"].exists()
    assert paths["summary_csv"].exists()

    analyzer = CrossoverAnalyzer(results_dir=tmp_path)
    cx_report = analyzer.run_full_analysis()

    lat_cxs = [
        t for t in cx_report["crossover_thresholds"] if t["axis"] == "network_latency"
    ]
    assert len(lat_cxs) == 1
    assert lat_cxs[0]["crossover_observed"] is True
    assert 10.0 <= lat_cxs[0]["interpolated_threshold"] <= 50.0

    graph_gen = AcademicGraphGenerator(results_dir=tmp_path)
    graphs = graph_gen.generate_all_graphs()
    for g_key, g_files in graphs.items():
        assert g_files["png"].exists(), f"Missing PNG for {g_key}"
        assert g_files["pdf"].exists(), f"Missing PDF for {g_key}"
