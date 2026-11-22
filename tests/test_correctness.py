"""Cross-Engine Equivalence, Network Shaping, and Traffic Monitoring Tests."""

from dataset import DatasetGenerator
from fhe import FHEBenchmarkEngine
from smpc import SMPCBenchmarkEngine
from networking import NetworkEmulator, NetworkTrafficMonitor
from orchestrator import CrossEngineValidator


def test_cross_engine_equivalence_both_workloads():
    ds = DatasetGenerator().generate("small", seed=42)
    fhe_eng = FHEBenchmarkEngine()
    smpc_eng = SMPCBenchmarkEngine()

    for wl in ("aggregation", "risk_multiplication"):
        fhe_res = fhe_eng.run_pipeline(ds, workload=wl, profile_name="threshold_bfv_128")
        smpc_res = smpc_eng.run_pipeline(
            ds, workload=wl, protocol_key="semi_dishonest_majority_3p"
        )
        ver = CrossEngineValidator.validate_pair(ds, wl, fhe_res, smpc_res)
        assert ver["exact_match"] is True
        assert ver["fhe_value"] == ver["smpc_value"] == ver["reference_value"]
        assert "DIRECTLY_EQUIVALENT" in ver["trust_comparison_label"]


def test_trust_model_divergence_labeling():
    ds = DatasetGenerator().generate("small", seed=42)
    fhe_eng = FHEBenchmarkEngine()
    smpc_eng = SMPCBenchmarkEngine()

    fhe_single = fhe_eng.run_pipeline(
        ds, workload="aggregation", profile_name="single_key_bfv_128"
    )
    smpc_shamir = smpc_eng.run_pipeline(
        ds, workload="aggregation", protocol_key="shamir_honest_majority_3p"
    )
    ver = CrossEngineValidator.validate_pair(ds, "aggregation", fhe_single, smpc_shamir)
    assert ver["exact_match"] is True
    assert "NON_EQUIVALENT_TRUST_MODEL" in ver["trust_comparison_label"]


def test_network_emulation_rtt_and_cleanup():
    emu = NetworkEmulator()
    with emu.emulate_profile(latency_profile="fast_wan", bandwidth_profile="medium") as st:
        assert st.configured_one_way_delay_ms == 10.0
        assert st.configured_bandwidth_mbps == 100.0
        if st.tc_shaping_active:
            assert st.measured_rtt_ms >= 15.0

    # After context exit, qdisc should be restored to local baseline (< 5 ms RTT)
    restored_rtt = emu.latency_ctrl.measure_actual_rtt_ms()
    assert restored_rtt < 5.0


def test_traffic_monitor_no_double_counting():
    mon = NetworkTrafficMonitor("lo")
    mon.start()
    from fhe.key_generation import transmit_payload_over_tcp
    sent_bytes, _ = transmit_payload_over_tcp([b"A" * 50000, b"B" * 50000])
    assert sent_bytes == 100000
    delta = mon.stop(fallback_protocol_bytes=sent_bytes)
    # Aggregate traffic uses tx_bytes delta (not tx + rx) to avoid double counting
    assert delta.aggregate_network_traffic_bytes >= 100000
    assert delta.aggregate_network_traffic_bytes < 200000
