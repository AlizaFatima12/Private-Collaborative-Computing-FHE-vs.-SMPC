"""Interactive and Scriptable CLI Demo for 3-Hospital Collaborative Disease Research."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, Optional

from demo.runner import list_demo_options, run_hospital_demo


def _prompt_choice(title: str, options: list[str], default: str, labels: Optional[Dict[str, str]] = None) -> str:
    print(f"\n{title}:")
    for idx, opt in enumerate(options, start=1):
        desc = f" - {labels[opt]}" if labels and opt in labels else ""
        marker = " (default)" if opt == default else ""
        print(f"  [{idx}] {opt}{desc}{marker}")
    raw = input(f"Select [1-{len(options)}] or press Enter for '{default}': ").strip()
    if not raw:
        return default
    if raw.isdigit():
        i = int(raw) - 1
        if 0 <= i < len(options):
            return options[i]
    if raw in options:
        return raw
    print(f"Invalid selection '{raw}', using default '{default}'.")
    return default


def _print_banner(text: str) -> None:
    line = "=" * 78
    print(f"\n{line}\n{text}\n{line}")


def render_cli_report(result: Dict[str, Any]) -> None:
    """Formats a clear, presentation-friendly walkthrough of the live 3-hospital run."""
    cohort = result["cohort_summary"]
    net = result["network"]
    fhe = result["fhe"]
    smpc = result["smpc"]
    val = result["validation"]
    cmp = result["comparison"]

    _print_banner(
        "PRIVATE COLLABORATIVE COMPUTING — 3-HOSPITAL DISEASE RESEARCH DEMO"
    )
    print(f"Cohort Profile : {cohort['profile_name']} (Total Patients N = {cohort['total_patients']:,})")
    print(f"Workload       : {cohort['workload']}")
    print(
        f"Network Link   : {net['latency_profile']} ({net['configured_latency_ms']} ms one-way, "
        f"measured RTT={net['measured_rtt_ms']} ms) | {net['bandwidth_profile']} ({net['configured_bandwidth_mbps']} Mbps) "
        f"| tc/netem={'ACTIVE' if net['tc_shaping_active'] else 'OFF'}"
    )

    print("\n[Step 1] Private Regional Hospital Cohorts (Never Shared Raw)")
    print("-" * 78)
    print(f"{'Hospital':<44} {'Patients':>8} {'Avg Biomarker':>14} {'Avg Severity':>13}")
    for h in cohort["hospitals"]:
        print(
            f"{h['organization']:<44} {h['patient_count']:>8} "
            f"{h['local_biomarker_mean']:>14.2f} {h['local_severity_mean']:>13.2f}"
        )
        if h["sample_records"]:
            s0 = h["sample_records"][0]
            print(
                f"    Sample private row: {s0.get('record_id')} | "
                f"biomarker={s0.get('biomarker_score')} | severity={s0.get('severity_weight')} "
                f"| stage={s0.get('diagnosis_stage', 'N/A')}"
            )

    print("\n[Step 2] OpenFHE Engine (3-of-3 Threshold BFV-RNS — Compute on Encrypted Boxes)")
    print("-" * 78)
    print(f"  Protocol / Security  : {fhe['protocol']} ({fhe['security_model']})")
    print(f"  Ring & Modulus       : n={fhe['fhe_ring_dimension']}, p={fhe['fhe_plaintext_modulus']}, log2Q={fhe['fhe_total_modulus_bits']} bits")
    print(f"  KeyGen / Setup Time  : {fhe['setup_time_s']:.4f} s (pk={fhe['fhe_public_key_size_bytes']/1024:.1f} KB, evk={fhe['fhe_eval_mult_key_size_bytes']/1024:.1f} KB)")
    print(f"  Encrypt + Upload     : {fhe['encryption_time_s']:.4f} s ({fhe['fhe_num_ciphertexts_total']} SIMD ciphertexts, {fhe['expansion_ratio']:.1f}x expansion)")
    print(f"  Homomorphic Eval     : {fhe['computation_time_s']:.4f} s (adds={fhe['fhe_num_homomorphic_additions']}, mults={fhe['fhe_num_homomorphic_multiplications']})")
    print(f"  3-of-3 Decryption    : {fhe['decryption_time_s']:.4f} s (remaining noise budget: {fhe['fhe_estimated_remaining_noise_budget_bits']:.2f} bits)")
    print(f"  Total Time & Traffic : {fhe['total_execution_time_s']:.4f} s | {cmp['fhe_traffic_mb']:.3f} MB | {cmp['fhe_rounds']} rounds")
    print(f"  Decrypted Answer     : {fhe['computed_value']:,} (expected {fhe['expected_value']:,})")

    print("\n[Step 3] MP-SPDZ Engine (3-Party Secret Sharing — Distributed Shares & Beaver Triples)")
    print("-" * 78)
    print(f"  Protocol / Binary    : {smpc['protocol']} ({smpc['smpc_binary_online']})")
    print(f"  Secret Sharing       : {smpc['scheme']} ({smpc['expansion_ratio']:.1f}x total share size across 3 hospitals)")
    print(f"  Beaver Preprocessing : {smpc['preprocessing_time_s']:.4f} s (triples consumed={smpc['beaver_triples_consumed']:,}, reuse prevented={smpc['smpc_triple_reuse_prevented']})")
    print(f"  Input Secret Sharing : {smpc['input_protect_time_s']:.4f} s")
    print(f"  Online Computation   : {smpc['computation_time_s']:.4f} s")
    print(f"  Output Reconstruction: {smpc['output_recover_time_s']:.4f} s")
    print(f"  Total Time & Traffic : {smpc['total_execution_time_s']:.4f} s | {cmp['smpc_traffic_mb']:.3f} MB | {cmp['smpc_rounds']} rounds")
    print(f"  Reconstructed Answer : {smpc['computed_value']:,} (expected {smpc['expected_value']:,})")

    print("\n[Step 4] Cross-Engine Verification & Operational Winner")
    print("-" * 78)
    print(f"  Exact Match Verified : {val['exact_match']} (FHE == SMPC == Plaintext Reference = {val['reference_value']:,})")
    print(f"  Trust Alignment      : {val['trust_comparison_label']}")
    print(
        f"  Faster Engine        : {cmp['faster_technology']} "
        f"({cmp['speedup_factor']:.2f}x faster | FHE={cmp['fhe_total_time_s']:.4f}s vs SMPC={cmp['smpc_total_time_s']:.4f}s)"
    )
    print("=" * 78)


def main() -> None:
    opts = list_demo_options()
    parser = argparse.ArgumentParser(
        description="Interactive CLI Demo: 3-Hospital Collaborative Disease Research (FHE vs. SMPC)"
    )
    parser.add_argument("--interactive", action="store_true", help="Launch interactive step-by-step prompt menu.")
    parser.add_argument("--size", default="small", help="Cohort size profile (small, medium, large, very_large, stress_test) or integer.")
    parser.add_argument("--workload", default="risk_multiplication", choices=list(opts["workloads"].keys()))
    parser.add_argument("--latency", default="local", choices=list(opts["latency_profiles"].keys()))
    parser.add_argument("--bandwidth", default="high", choices=list(opts["bandwidth_profiles"].keys()))
    parser.add_argument("--fhe-profile", default="threshold_bfv_128", choices=opts["fhe_profiles"])
    parser.add_argument("--smpc-protocol", default="semi_dishonest_majority_3p", choices=opts["smpc_protocols"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--no-netem", action="store_true", help="Skip Linux tc/netem shaping.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result instead of formatted report.")
    parser.add_argument(
        "--csvs",
        nargs=3,
        metavar=("NORTH_CSV", "COASTAL_CSV", "URBAN_CSV"),
        help="Optional paths to 3 hospital CSV files (biomarker_score,severity_weight).",
    )
    args = parser.parse_args()

    # If user invokes `python3 -m demo.cli` with no args on an interactive terminal, show menu
    use_menu = args.interactive or (len(sys.argv) == 1 and sys.stdin.isatty())
    if use_menu:
        _print_banner("3-HOSPITAL COLLABORATIVE DISEASE RESEARCH — INTERACTIVE DEMO")
        size = _prompt_choice(
            "1. Select total patient cohort size",
            list(opts["dataset_profiles"].keys()),
            "small",
            {k: f"{v:,} patients" for k, v in opts["dataset_profiles"].items()},
        )
        workload = _prompt_choice(
            "2. Select collaborative clinical workload",
            list(opts["workloads"].keys()),
            "risk_multiplication",
            opts["workloads"],
        )
        latency = _prompt_choice(
            "3. Select inter-hospital WAN latency",
            list(opts["latency_profiles"].keys()),
            "local",
            opts["latency_profiles"],
        )
        bandwidth = _prompt_choice(
            "4. Select link bandwidth limit",
            list(opts["bandwidth_profiles"].keys()),
            "high",
            opts["bandwidth_profiles"],
        )
        fhe_prof = _prompt_choice(
            "5. Select FHE parameter profile",
            opts["fhe_profiles"],
            "threshold_bfv_128",
        )
        smpc_proto = _prompt_choice(
            "6. Select SMPC protocol",
            opts["smpc_protocols"],
            "semi_dishonest_majority_3p",
        )
    else:
        size = args.size
        workload = args.workload
        latency = args.latency
        bandwidth = args.bandwidth
        fhe_prof = args.fhe_profile
        smpc_proto = args.smpc_protocol

    custom_csvs = None
    if args.csvs:
        custom_csvs = {
            "participant_a": Path(args.csvs[0]).read_text(encoding="utf-8"),
            "participant_b": Path(args.csvs[1]).read_text(encoding="utf-8"),
            "participant_c": Path(args.csvs[2]).read_text(encoding="utf-8"),
        }

    print("\nRunning live 3-hospital OpenFHE + MP-SPDZ comparison...")
    res = run_hospital_demo(
        size=size,
        workload=workload,
        latency_profile=latency,
        bandwidth_profile=bandwidth,
        fhe_profile=fhe_prof,
        smpc_protocol=smpc_proto,
        seed=args.seed,
        apply_network=not args.no_netem,
        custom_csvs=custom_csvs,
    )

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        render_cli_report(res)


if __name__ == "__main__":
    main()
