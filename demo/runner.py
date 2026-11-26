"""Shared Demo Orchestration Engine for CLI and Web GUI Hospital Demonstrations."""

import csv
import io
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml

from dataset import (
    DatasetGenerator,
    DatasetValidator,
    CollaborativeDataset,
    ParticipantPartition,
    PlaintextReferenceResult,
)
from fhe import FHEBenchmarkEngine
from smpc import SMPCBenchmarkEngine
from networking import NetworkEmulator, NetworkTrafficMonitor
from orchestrator import CrossEngineValidator


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def list_demo_options() -> Dict[str, Any]:
    """Loads valid configuration profiles and available generated report artifacts."""
    root = _root_dir()
    cfg_dir = root / "config"
    with open(cfg_dir / "experiment.yaml", "r", encoding="utf-8") as f:
        exp_cfg = yaml.safe_load(f) or {}
    with open(cfg_dir / "network.yaml", "r", encoding="utf-8") as f:
        net_cfg = yaml.safe_load(f) or {}
    with open(cfg_dir / "fhe.yaml", "r", encoding="utf-8") as f:
        fhe_cfg = yaml.safe_load(f) or {}
    with open(cfg_dir / "smpc.yaml", "r", encoding="utf-8") as f:
        smpc_cfg = yaml.safe_load(f) or {}

    results_dir = root / "results"
    graphs = sorted(
        p.name for p in (results_dir / "graphs").glob("*.png")
    ) if (results_dir / "graphs").exists() else []

    return {
        "participants": exp_cfg.get("experiment", {}).get("participants", []),
        "dataset_profiles": exp_cfg.get("datasets", {}).get("profiles", {}),
        "workloads": {
            "aggregation": "Collaborative Aggregation (Sum, Count & Mean)",
            "risk_multiplication": "Collaborative Inner-Product (Sum of Value * Weight)",
        },
        "latency_profiles": {
            k: f"{v.get('label', k)} ({v.get('one_way_delay_ms')} ms)"
            for k, v in net_cfg.get("network", {}).get("latency_profiles", {}).items()
        },
        "bandwidth_profiles": {
            k: f"{v.get('label', k)}"
            for k, v in net_cfg.get("network", {}).get("bandwidth_profiles", {}).items()
        },
        "fhe_profiles": [
            k for k, v in fhe_cfg.get("fhe", {}).get("profiles", {}).items()
            if v.get("mode") != "bootstrapping_experiment"
        ],
        "smpc_protocols": list(smpc_cfg.get("smpc", {}).get("protocols", {}).keys()),
        "available_graphs": graphs,
    }


def load_decision_matrix() -> List[Dict[str, str]]:
    """Loads the empirically derived decision matrix CSV if generated."""
    dm_path = _root_dir() / "results" / "processed" / "decision_matrix.csv"
    if not dm_path.exists():
        return []
    with open(dm_path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_crossover_highlights() -> List[Dict[str, Any]]:
    """Loads observed performance crossover thresholds from crossover_thresholds.csv."""
    cx_path = _root_dir() / "results" / "processed" / "crossover_thresholds.csv"
    if not cx_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(cx_path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if str(row.get("crossover_observed", "")).lower() == "true":
                out.append({
                    "axis": row.get("axis", ""),
                    "workload": row.get("workload", ""),
                    "fixed_condition": row.get("fixed_condition", ""),
                    "transition": row.get("transition", ""),
                    "measured_bracket": row.get("measured_bracket", ""),
                    "interpolated_threshold": row.get("interpolated_threshold", ""),
                    "x_unit": row.get("x_unit", ""),
                })
    return out


def _build_dataset_from_inline_csvs(
    custom_csvs: Dict[str, str],
    seed: int = 42,
) -> CollaborativeDataset:
    """Parses 3 inline CSV texts (biomarker_score,severity_weight) into a CollaborativeDataset."""
    import tempfile

    gen = DatasetGenerator()
    gen.default_seed = seed
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_paths: Dict[str, Path] = {}
        for p_id, csv_text in custom_csvs.items():
            # Validate non-empty CSV rows before loading
            rows = list(csv.DictReader(io.StringIO(csv_text.strip())))
            if not rows:
                raise ValueError(f"Custom CSV for {p_id} contains no patient rows.")
            p_file = Path(tmp_dir) / f"{p_id}.csv"
            p_file.write_text(csv_text.strip() + "\n", encoding="utf-8")
            tmp_paths[p_id] = p_file
        ds = gen.from_csv_files(tmp_paths, profile_name="custom_hospital_cohort")
    DatasetValidator.validate_dataset(ds, plaintext_modulus=536903681)
    return ds


def _summarize_hospital_cohorts(ds: CollaborativeDataset, sample_rows: int = 4) -> List[Dict[str, Any]]:
    """Extracts per-hospital summary stats, diagnosis stage counts, and sample local records."""
    summaries: List[Dict[str, Any]] = []
    for part in ds.get_ordered_partitions():
        bio = part.biomarker_scores
        sev = part.severity_weights
        n = part.count
        stage_counts: Dict[str, int] = {}
        if part.patient_records:
            for rec in part.patient_records:
                stage_counts[rec.diagnosis_stage] = stage_counts.get(rec.diagnosis_stage, 0) + 1
            samples = [rec.to_dict() for rec in part.patient_records[:sample_rows]]
        else:
            samples = [
                {
                    "record_id": f"PAT-{i + 1:06d}",
                    "biomarker_score": bio[i],
                    "severity_weight": sev[i],
                }
                for i in range(min(sample_rows, n))
            ]
        summaries.append({
            "participant_id": part.participant_id,
            "organization": part.organization,
            "region": part.region,
            "patient_count": n,
            "local_biomarker_sum": int(sum(bio)),
            "local_biomarker_mean": round(sum(bio) / n, 2) if n else 0.0,
            "local_severity_mean": round(sum(sev) / n, 2) if n else 0.0,
            "local_risk_total": int(sum(x * w for x, w in zip(bio, sev))),
            "stage_counts": stage_counts,
            "sample_records": samples,
        })
    return summaries


def preview_hospital_cohorts(
    size: str | int = "small",
    seed: int = 42,
    custom_csvs: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Generates or loads the 3 hospital cohorts without running cryptography yet."""
    if custom_csvs:
        ds = _build_dataset_from_inline_csvs(custom_csvs, seed=seed)
    else:
        ds = DatasetGenerator().generate(size, seed=seed, materialize_records=True)
    return {
        "profile_name": ds.profile_name,
        "total_patients": ds.total_patients,
        "seed": ds.random_seed,
        "hospitals": _summarize_hospital_cohorts(ds),
        "reference": ds.reference.to_dict(),
    }


def run_hospital_demo(
    size: str | int = "small",
    workload: str = "risk_multiplication",
    latency_profile: str = "local",
    bandwidth_profile: str = "high",
    fhe_profile: str = "threshold_bfv_128",
    smpc_protocol: str = "semi_dishonest_majority_3p",
    seed: int = 42,
    apply_network: bool = True,
    custom_csvs: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Runs a live end-to-end 3-hospital collaborative computation comparison between
    OpenFHE and MP-SPDZ, returning full hospital previews, network state, phase metrics,
    and cross-engine verification results.
    """
    if custom_csvs:
        ds = _build_dataset_from_inline_csvs(custom_csvs, seed=seed)
    else:
        materialize = True
        if isinstance(size, int) and size > 10000:
            materialize = False
        elif isinstance(size, str) and size.lower() in ("very_large", "stress_test"):
            materialize = False
        ds = DatasetGenerator().generate(size, seed=seed, materialize_records=materialize)

    fhe_engine = FHEBenchmarkEngine()
    smpc_engine = SMPCBenchmarkEngine()
    net_emu = NetworkEmulator()

    def _run_both(net_state_dict: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        fhe_mon = NetworkTrafficMonitor(net_emu.interface)
        fhe_mon.start()
        fhe_res = fhe_engine.run_pipeline(
            dataset=ds,
            workload=workload,
            profile_name=fhe_profile,
            enable_network_transport=True,
        )
        fhe_traffic = fhe_mon.stop(fallback_protocol_bytes=int(fhe_res["protocol_network_bytes"]))
        fhe_res.update(net_state_dict)
        fhe_res.update(fhe_traffic.to_metrics_dict())

        smpc_mon = NetworkTrafficMonitor(net_emu.interface)
        smpc_mon.start()
        smpc_res = smpc_engine.run_pipeline(
            dataset=ds,
            workload=workload,
            protocol_key=smpc_protocol,
        )
        smpc_traffic = smpc_mon.stop(fallback_protocol_bytes=int(smpc_res["protocol_network_bytes"]))
        smpc_res.update(net_state_dict)
        smpc_res.update(smpc_traffic.to_metrics_dict())
        return fhe_res, smpc_res

    if apply_network:
        with net_emu.emulate_profile(
            latency_profile=latency_profile,
            bandwidth_profile=bandwidth_profile,
        ) as net_state:
            net_dict = net_state.to_metrics_dict()
            net_dict["tc_qdisc_summary"] = net_state.tc_qdisc_summary
            fhe_res, smpc_res = _run_both(net_dict)
    else:
        lat_spec = net_emu.latency_ctrl.get_profile(latency_profile)
        bw_spec = net_emu.bandwidth_ctrl.get_profile(bandwidth_profile)
        net_dict = {
            "network_interface": net_emu.interface,
            "latency_profile": lat_spec.profile_name,
            "bandwidth_profile": bw_spec.profile_name,
            "configured_latency_ms": lat_spec.one_way_delay_ms,
            "expected_rtt_ms": lat_spec.expected_rtt_ms,
            "measured_rtt_ms": 0.0,
            "configured_bandwidth_mbps": bw_spec.rate_mbps,
            "configured_packet_loss_pct": 0.0,
            "tc_shaping_active": False,
            "tc_qdisc_summary": "disabled_by_user",
        }
        fhe_res, smpc_res = _run_both(net_dict)

    validation = CrossEngineValidator.validate_pair(
        dataset=ds,
        workload=workload,
        fhe_result=fhe_res,
        smpc_result=smpc_res,
    )

    fhe_t = float(fhe_res["total_execution_time_s"])
    smpc_t = float(smpc_res["total_execution_time_s"])
    faster = "FHE" if fhe_t <= smpc_t else "SMPC"
    speedup = (max(fhe_t, smpc_t) / min(fhe_t, smpc_t)) if min(fhe_t, smpc_t) > 0 else 1.0

    return {
        "cohort_summary": {
            "profile_name": ds.profile_name,
            "total_patients": ds.total_patients,
            "workload": workload,
            "hospitals": _summarize_hospital_cohorts(ds),
            "reference": ds.reference.to_dict(),
        },
        "network": net_dict,
        "fhe": fhe_res,
        "smpc": smpc_res,
        "validation": validation,
        "comparison": {
            "faster_technology": faster,
            "speedup_factor": round(speedup, 3),
            "time_diff_s": round(abs(fhe_t - smpc_t), 6),
            "fhe_total_time_s": fhe_t,
            "smpc_total_time_s": smpc_t,
            "fhe_traffic_mb": fhe_res.get("measured_network_traffic_mb", 0.0),
            "smpc_traffic_mb": smpc_res.get("measured_network_traffic_mb", 0.0),
            "fhe_rounds": fhe_res.get("communication_rounds", 0),
            "smpc_rounds": smpc_res.get("communication_rounds", 0),
        },
    }


def _parse_client_csvs(clients: List[Dict[str, str]]) -> tuple[List[str], List[str], List[Dict[str, Any]]]:
    """
    Parses 3 client CSV payloads and enforces that all 3 have identical columns.
    Returns (ordered_columns, numeric_columns, parsed_client_entries).
    """
    if len(clients) != 3:
        raise ValueError(f"Exactly 3 clients are required; received {len(clients)}.")

    parsed_entries: List[Dict[str, Any]] = []
    ref_cols: Optional[List[str]] = None

    for idx, c in enumerate(clients):
        name = str(c.get("name") or f"Client {idx + 1}").strip()
        tag = str(c.get("tag") or f"Site {idx + 1}").strip()
        csv_text = str(c.get("csv_text") or "").strip()
        if not csv_text:
            raise ValueError(f"{name}: CSV file is empty. Please select a CSV dataset from your PC.")

        reader = csv.DictReader(io.StringIO(csv_text))
        if not reader.fieldnames:
            raise ValueError(f"{name}: CSV has no header row.")
        cols = [col.strip() for col in reader.fieldnames if col is not None and col.strip()]
        if not cols:
            raise ValueError(f"{name}: CSV header columns are empty.")

        rows: List[Dict[str, str]] = []
        for raw_row in reader:
            cleaned = {
                (k.strip() if k else ""): (v.strip() if v is not None else "")
                for k, v in raw_row.items()
                if k is not None and k.strip()
            }
            if any(cleaned.values()):
                rows.append(cleaned)

        if not rows:
            raise ValueError(f"{name}: CSV contains headers ({cols}) but 0 data rows.")

        if ref_cols is None:
            ref_cols = cols
        else:
            missing = [col for col in ref_cols if col not in cols]
            extra = [col for col in cols if col not in ref_cols]
            if missing or extra:
                raise ValueError(
                    f"Column mismatch in '{name}': all 3 datasets must have the exact same columns. "
                    f"Missing={missing or 'none'}, Extra={extra or 'none'} (expected {ref_cols})."
                )

        parsed_entries.append({
            "participant_id": f"participant_{chr(97 + idx)}",
            "index": idx,
            "name": name,
            "tag": tag,
            "columns": cols,
            "rows": rows,
        })

    assert ref_cols is not None
    numeric_cols: List[str] = []
    for col in ref_cols:
        is_num = True
        for entry in parsed_entries:
            for row in entry["rows"]:
                val_str = row.get(col, "")
                if val_str == "":
                    is_num = False
                    break
                try:
                    val_f = float(val_str)
                    if not math.isfinite(val_f):
                        is_num = False
                        break
                except ValueError:
                    is_num = False
                    break
            if not is_num:
                break
        if is_num:
            numeric_cols.append(col)

    if not numeric_cols:
        raise ValueError(
            f"No shared numeric columns found across the 3 CSV files (headers: {ref_cols}). "
            "At least one numeric column is required for FHE/SMPC computation."
        )

    return ref_cols, numeric_cols, parsed_entries


def inspect_dynamic_csvs(clients: List[Dict[str, str]]) -> Dict[str, Any]:
    """Validates 3 user-provided client CSVs, checks matching columns, and returns previews."""
    cols, numeric_cols, entries = _parse_client_csvs(clients)
    client_summaries: List[Dict[str, Any]] = []
    for entry in entries:
        col_means: Dict[str, float] = {}
        for nc in numeric_cols:
            vals = [float(r[nc]) for r in entry["rows"]]
            col_means[nc] = round(sum(vals) / len(vals), 4)
        client_summaries.append({
            "participant_id": entry["participant_id"],
            "name": entry["name"],
            "tag": entry["tag"],
            "row_count": len(entry["rows"]),
            "column_means": col_means,
            "preview_rows": entry["rows"][:5],
        })
    return {
        "columns": cols,
        "numeric_columns": numeric_cols,
        "total_rows": sum(len(e["rows"]) for e in entries),
        "clients": client_summaries,
    }


def _build_dynamic_dataset(
    clients: List[Dict[str, str]],
    value_col: str,
    weight_col: Optional[str] = None,
    scale_mode: str = "auto",
) -> tuple[CollaborativeDataset, Dict[str, Any]]:
    """Constructs a CollaborativeDataset from 3 matching client CSVs."""
    cols, numeric_cols, entries = _parse_client_csvs(clients)
    if value_col not in numeric_cols:
        raise ValueError(f"Selected value column '{value_col}' is not a valid shared numeric column ({numeric_cols}).")
    active_weight_col = weight_col if (weight_col and weight_col in numeric_cols) else value_col

    raw_x_per_client = [[float(r[value_col]) for r in e["rows"]] for e in entries]
    raw_w_per_client = [
        [float(r[active_weight_col]) for r in e["rows"]] if weight_col else [1.0] * len(e["rows"])
        for e in entries
    ]

    def _choose_scale(lists_of_floats: List[List[float]]) -> int:
        if scale_mode != "auto":
            return max(1, int(scale_mode))
        all_vals = [v for sub in lists_of_floats for v in sub]
        if all(abs(v - round(v)) < 1e-9 for v in all_vals):
            return 1
        return 100

    scale_x = _choose_scale(raw_x_per_client)
    scale_w = _choose_scale(raw_w_per_client) if weight_col else 1

    partitions: Dict[str, ParticipantPartition] = {}
    participant_sums: Dict[str, int] = {}
    participant_counts: Dict[str, int] = {}
    participant_risk_totals: Dict[str, int] = {}
    all_x_ints: List[int] = []
    all_w_ints: List[int] = []
    client_details: List[Dict[str, Any]] = []

    for entry, rx, rw in zip(entries, raw_x_per_client, raw_w_per_client):
        p_id = entry["participant_id"]
        x_ints = [int(round(v * scale_x)) for v in rx]
        w_ints = [int(round(v * scale_w)) for v in rw]
        all_x_ints.extend(x_ints)
        all_w_ints.extend(w_ints)

        partitions[p_id] = ParticipantPartition(
            participant_id=p_id,
            participant_index=entry["index"],
            organization=entry["name"],
            region=entry["tag"],
            transactions=[],
            amounts=x_ints,
            risk_scores=w_ints,
        )
        p_sum = int(sum(x_ints))
        p_risk = int(sum(a * b for a, b in zip(x_ints, w_ints)))
        participant_sums[p_id] = p_sum
        participant_counts[p_id] = len(x_ints)
        participant_risk_totals[p_id] = p_risk

        client_details.append({
            "participant_id": p_id,
            "name": entry["name"],
            "tag": entry["tag"],
            "row_count": len(x_ints),
            "local_value_sum": round(sum(rx), 4),
            "local_value_mean": round(sum(rx) / len(rx), 4),
            "local_weight_mean": round(sum(rw) / len(rw), 4),
            "local_weighted_product": round(sum(a * b for a, b in zip(rx, rw)), 4),
            "preview_rows": entry["rows"][:5],
        })

    total_n = int(sum(participant_counts.values()))
    total_sum = int(sum(participant_sums.values()))
    exact_avg = float(total_sum) / float(total_n)
    int_avg = total_sum // total_n
    risk_total = int(sum(participant_risk_totals.values()))

    reference = PlaintextReferenceResult(
        total_sum=total_sum,
        total_count=total_n,
        exact_average=exact_avg,
        integer_average=int_avg,
        risk_total=risk_total,
        participant_sums=participant_sums,
        participant_counts=participant_counts,
        participant_risk_totals=participant_risk_totals,
    )

    ds = CollaborativeDataset(
        profile_name=f"dynamic_3client_{total_n}",
        total_transactions=total_n,
        random_seed=42,
        num_participants=3,
        partitions=partitions,
        reference=reference,
        amount_min=min(all_x_ints),
        amount_max=max(all_x_ints),
        risk_score_min=min(all_w_ints),
        risk_score_max=max(all_w_ints),
    )
    DatasetValidator.validate_dataset(ds, plaintext_modulus=536903681)

    meta = {
        "columns": cols,
        "numeric_columns": numeric_cols,
        "value_col": value_col,
        "weight_col": active_weight_col if weight_col else None,
        "scale_x": scale_x,
        "scale_w": scale_w,
        "total_records": total_n,
        "clients": client_details,
        "unscaled_value_sum": round(total_sum / scale_x, 4),
        "unscaled_value_mean": round(exact_avg / scale_x, 4),
        "unscaled_weighted_product": round(risk_total / (scale_x * scale_w), 4),
    }
    return ds, meta


def _execute_pair_once(
    ds: CollaborativeDataset,
    workload: str,
    latency_profile: str,
    bandwidth_profile: str,
    fhe_profile: str,
    smpc_protocol: str,
    apply_network: bool,
) -> Dict[str, Any]:
    """Runs FHE + SMPC once on `ds` under the given network and protocol configuration."""
    fhe_engine = FHEBenchmarkEngine()
    smpc_engine = SMPCBenchmarkEngine()
    net_emu = NetworkEmulator()

    def _run_both(net_state_dict: Dict[str, Any]) -> tuple[Dict[str, Any], Dict[str, Any]]:
        fhe_mon = NetworkTrafficMonitor(net_emu.interface)
        fhe_mon.start()
        fhe_res = fhe_engine.run_pipeline(
            dataset=ds,
            workload=workload,
            profile_name=fhe_profile,
            enable_network_transport=True,
        )
        fhe_traffic = fhe_mon.stop(fallback_protocol_bytes=int(fhe_res["protocol_network_bytes"]))
        fhe_res.update(net_state_dict)
        fhe_res.update(fhe_traffic.to_metrics_dict())

        smpc_mon = NetworkTrafficMonitor(net_emu.interface)
        smpc_mon.start()
        smpc_res = smpc_engine.run_pipeline(
            dataset=ds,
            workload=workload,
            protocol_key=smpc_protocol,
        )
        smpc_traffic = smpc_mon.stop(fallback_protocol_bytes=int(smpc_res["protocol_network_bytes"]))
        smpc_res.update(net_state_dict)
        smpc_res.update(smpc_traffic.to_metrics_dict())
        return fhe_res, smpc_res

    if apply_network:
        with net_emu.emulate_profile(
            latency_profile=latency_profile,
            bandwidth_profile=bandwidth_profile,
        ) as net_state:
            net_dict = net_state.to_metrics_dict()
            net_dict["tc_qdisc_summary"] = net_state.tc_qdisc_summary
            fhe_res, smpc_res = _run_both(net_dict)
    else:
        lat_spec = net_emu.latency_ctrl.get_profile(latency_profile)
        bw_spec = net_emu.bandwidth_ctrl.get_profile(bandwidth_profile)
        net_dict = {
            "network_interface": net_emu.interface,
            "latency_profile": lat_spec.profile_name,
            "bandwidth_profile": bw_spec.profile_name,
            "configured_latency_ms": lat_spec.one_way_delay_ms,
            "expected_rtt_ms": lat_spec.expected_rtt_ms,
            "measured_rtt_ms": 0.0,
            "configured_bandwidth_mbps": bw_spec.rate_mbps,
            "configured_packet_loss_pct": 0.0,
            "tc_shaping_active": False,
            "tc_qdisc_summary": "disabled_by_user",
        }
        fhe_res, smpc_res = _run_both(net_dict)

    validation = CrossEngineValidator.validate_pair(
        dataset=ds,
        workload=workload,
        fhe_result=fhe_res,
        smpc_result=smpc_res,
    )

    fhe_t = float(fhe_res["total_execution_time_s"])
    smpc_t = float(smpc_res["total_execution_time_s"])
    faster = "FHE" if fhe_t <= smpc_t else "SMPC"
    speedup = (max(fhe_t, smpc_t) / min(fhe_t, smpc_t)) if min(fhe_t, smpc_t) > 0 else 1.0

    return {
        "network": net_dict,
        "fhe": fhe_res,
        "smpc": smpc_res,
        "validation": validation,
        "comparison": {
            "faster_technology": faster,
            "speedup_factor": round(speedup, 3),
            "time_diff_s": round(abs(fhe_t - smpc_t), 6),
            "fhe_total_time_s": fhe_t,
            "smpc_total_time_s": smpc_t,
            "fhe_traffic_mb": fhe_res.get("measured_network_traffic_mb", 0.0),
            "smpc_traffic_mb": smpc_res.get("measured_network_traffic_mb", 0.0),
            "fhe_rounds": fhe_res.get("communication_rounds", 0),
            "smpc_rounds": smpc_res.get("communication_rounds", 0),
        },
    }


def run_dynamic_demo(
    clients: List[Dict[str, str]],
    value_col: str,
    weight_col: Optional[str] = None,
    workload: str = "risk_multiplication",
    latency_profile: str = "local",
    bandwidth_profile: str = "high",
    fhe_profile: str = "threshold_bfv_128",
    smpc_protocol: str = "semi_dishonest_majority_3p",
    apply_network: bool = True,
    scale_mode: str = "auto",
) -> Dict[str, Any]:
    """Runs FHE vs. SMPC on 3 dynamic client CSV datasets."""
    ds, meta = _build_dynamic_dataset(
        clients=clients,
        value_col=value_col,
        weight_col=weight_col if workload == "risk_multiplication" else None,
        scale_mode=scale_mode,
    )
    run_out = _execute_pair_once(
        ds=ds,
        workload=workload,
        latency_profile=latency_profile,
        bandwidth_profile=bandwidth_profile,
        fhe_profile=fhe_profile,
        smpc_protocol=smpc_protocol,
        apply_network=apply_network,
    )
    return {
        "dataset_meta": meta,
        "workload": workload,
        **run_out,
    }


def run_dynamic_sweep(
    clients: List[Dict[str, str]],
    value_col: str,
    weight_col: Optional[str] = None,
    workload: str = "risk_multiplication",
    fhe_profile: str = "threshold_bfv_128",
    smpc_protocol: str = "semi_dishonest_majority_3p",
    apply_network: bool = True,
    scale_mode: str = "auto",
) -> Dict[str, Any]:
    """
    Runs the user's 3 client CSV datasets across multiple WAN latency and bandwidth profiles
    so the Graphs tab renders live crossover curves for their exact datasets.
    """
    ds, meta = _build_dynamic_dataset(
        clients=clients,
        value_col=value_col,
        weight_col=weight_col if workload == "risk_multiplication" else None,
        scale_mode=scale_mode,
    )

    latency_points: List[Dict[str, Any]] = []
    first_run: Optional[Dict[str, Any]] = None
    for lat_p in ("local", "fast_wan", "moderate_wan", "slow_wan"):
        res = _execute_pair_once(
            ds=ds,
            workload=workload,
            latency_profile=lat_p,
            bandwidth_profile="high",
            fhe_profile=fhe_profile,
            smpc_protocol=smpc_protocol,
            apply_network=apply_network,
        )
        if first_run is None:
            first_run = res
        latency_points.append({
            "latency_profile": lat_p,
            "latency_ms": res["network"]["configured_latency_ms"],
            "measured_rtt_ms": res["network"]["measured_rtt_ms"],
            "fhe_time_s": res["comparison"]["fhe_total_time_s"],
            "smpc_time_s": res["comparison"]["smpc_total_time_s"],
            "fhe_traffic_mb": res["comparison"]["fhe_traffic_mb"],
            "smpc_traffic_mb": res["comparison"]["smpc_traffic_mb"],
            "faster": res["comparison"]["faster_technology"],
        })

    bw_profiles = ("high", "medium", "restricted", "very_restricted") if ds.total_transactions <= 250 else ("high", "medium", "restricted")
    bandwidth_points: List[Dict[str, Any]] = []
    for bw_p in bw_profiles:
        res = _execute_pair_once(
            ds=ds,
            workload=workload,
            latency_profile="moderate_wan",
            bandwidth_profile=bw_p,
            fhe_profile=fhe_profile,
            smpc_protocol=smpc_protocol,
            apply_network=apply_network,
        )
        bandwidth_points.append({
            "bandwidth_profile": bw_p,
            "bandwidth_mbps": res["network"]["configured_bandwidth_mbps"],
            "fhe_time_s": res["comparison"]["fhe_total_time_s"],
            "smpc_time_s": res["comparison"]["smpc_total_time_s"],
            "fhe_traffic_mb": res["comparison"]["fhe_traffic_mb"],
            "smpc_traffic_mb": res["comparison"]["smpc_traffic_mb"],
            "faster": res["comparison"]["faster_technology"],
        })

    return {
        "dataset_meta": meta,
        "workload": workload,
        "latency_series": latency_points,
        "bandwidth_series": bandwidth_points,
        "sample_run": {
            "dataset_meta": meta,
            "workload": workload,
            **(first_run or {}),
        },
    }
