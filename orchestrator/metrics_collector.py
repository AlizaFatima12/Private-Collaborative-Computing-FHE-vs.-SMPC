"""Metrics Collector, Statistical Aggregator, and CSV/JSON Exporter."""

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional
import numpy as np
import pandas as pd
from scipy import stats


SUMMARY_METRIC_COLUMNS = (
    "total_execution_time_s",
    "setup_time_s",
    "preprocessing_time_s",
    "input_protect_time_s",
    "addition_time_s",
    "multiplication_time_s",
    "computation_time_s",
    "output_recover_time_s",
    "online_time_s",
    "measured_network_traffic_bytes",
    "measured_network_traffic_mb",
    "protocol_network_bytes",
    "crypto_payload_size_bytes",
    "plaintext_size_bytes",
    "expansion_ratio",
    "storage_overhead_bytes",
    "communication_rounds",
    "beaver_triples_consumed",
    "peak_memory_mb",
    "measured_rtt_ms",
)

GROUP_KEY_COLUMNS = (
    "experiment_dimension",
    "technology",
    "protocol",
    "scheme",
    "security_model",
    "trust_assumption",
    "trust_comparison_label",
    "workload",
    "dataset_profile",
    "num_transactions",
    "num_participants",
    "latency_profile",
    "configured_latency_ms",
    "expected_rtt_ms",
    "bandwidth_profile",
    "configured_bandwidth_mbps",
    "configured_packet_loss_pct",
)


class MetricsCollector:
    """Stores raw benchmark trials and calculates parametric & non-parametric statistical summaries."""

    def __init__(self, results_dir: Optional[str | Path] = None, confidence_level: float = 0.95):
        if results_dir is not None:
            self.results_dir = Path(results_dir)
        else:
            self.results_dir = Path(__file__).resolve().parent.parent / "results"

        self.raw_dir = self.results_dir / "raw"
        self.processed_dir = self.results_dir / "processed"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

        self.confidence_level = confidence_level
        self.raw_records: List[Dict[str, Any]] = []

    def add_trial_record(
        self,
        record: Dict[str, Any],
        experiment_dimension: str,
        repetition_index: int,
        is_warmup: bool = False,
    ) -> None:
        enriched = dict(record)
        enriched["trial_id"] = len(self.raw_records) + 1
        enriched["timestamp_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        enriched["experiment_dimension"] = experiment_dimension
        enriched["repetition_index"] = repetition_index
        enriched["is_warmup"] = bool(is_warmup)
        self.raw_records.append(enriched)

    def get_raw_dataframe(self, include_warmup: bool = False) -> pd.DataFrame:
        if not self.raw_records:
            return pd.DataFrame()
        df = pd.DataFrame(self.raw_records)
        if not include_warmup and "is_warmup" in df.columns:
            df = df[~df["is_warmup"]].copy()
        return df

    def compute_statistical_summaries(self) -> pd.DataFrame:
        """
        Aggregates measured repetitions (excluding warm-up trials) by experimental configuration,
        computing mean, median, std, min, max, and 95% Student-t confidence intervals.
        """
        df = self.get_raw_dataframe(include_warmup=False)
        if df.empty:
            return pd.DataFrame()

        active_group_cols = [c for c in GROUP_KEY_COLUMNS if c in df.columns]
        active_metric_cols = [c for c in SUMMARY_METRIC_COLUMNS if c in df.columns]

        summary_rows: List[Dict[str, Any]] = []
        alpha = 1.0 - self.confidence_level

        for group_vals, group_df in df.groupby(active_group_cols, dropna=False, sort=False):
            if not isinstance(group_vals, tuple):
                group_vals = (group_vals,)
            row: Dict[str, Any] = dict(zip(active_group_cols, group_vals))
            n_trials = len(group_df)
            row["num_repetitions"] = n_trials
            row["all_trials_correct"] = bool(group_df["correctness_verified"].all())

            # Carry over primary cryptographic metadata columns
            for meta_col in (
                "fhe_ring_dimension",
                "fhe_plaintext_modulus",
                "fhe_ciphertext_modulus_bits",
                "fhe_multiplicative_depth",
                "fhe_estimated_remaining_noise_budget_bits",
                "fhe_public_key_size_bytes",
                "fhe_eval_mult_key_size_bytes",
                "computed_value",
                "expected_value",
            ):
                if meta_col in group_df.columns:
                    first_valid = group_df[meta_col].dropna()
                    if not first_valid.empty:
                        row[meta_col] = first_valid.iloc[0]

            for m_col in active_metric_cols:
                vals = pd.to_numeric(group_df[m_col], errors="coerce").dropna().to_numpy(dtype=float)
                if len(vals) == 0:
                    continue
                mean_v = float(np.mean(vals))
                med_v = float(np.median(vals))
                std_v = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
                min_v = float(np.min(vals))
                max_v = float(np.max(vals))

                if len(vals) > 1 and std_v > 0:
                    t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df=len(vals) - 1))
                    margin = t_crit * (std_v / math.sqrt(len(vals)))
                else:
                    margin = 0.0

                row[f"{m_col}_mean"] = round(mean_v, 6)
                row[f"{m_col}_median"] = round(med_v, 6)
                row[f"{m_col}_std"] = round(std_v, 6)
                row[f"{m_col}_min"] = round(min_v, 6)
                row[f"{m_col}_max"] = round(max_v, 6)
                row[f"{m_col}_ci95_low"] = round(max(0.0, mean_v - margin), 6)
                row[f"{m_col}_ci95_high"] = round(mean_v + margin, 6)
                row[f"{m_col}_ci95_margin"] = round(margin, 6)

            summary_rows.append(row)

        return pd.DataFrame(summary_rows)

    def export_all(
        self,
        extra_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Path]:
        """Exports raw trials and statistical summaries to CSV and JSON files."""
        raw_all_df = self.get_raw_dataframe(include_warmup=True)
        summary_df = self.compute_statistical_summaries()

        raw_csv = self.raw_dir / "raw_benchmarks.csv"
        raw_json = self.raw_dir / "raw_benchmarks.json"
        sum_csv = self.processed_dir / "summary_benchmarks.csv"
        sum_json = self.processed_dir / "summary_benchmarks.json"
        meta_json = self.processed_dir / "experiment_metadata.json"

        raw_all_df.to_csv(raw_csv, index=False)
        with open(raw_json, "w", encoding="utf-8") as f:
            json.dump(self.raw_records, f, indent=2, default=str)

        summary_df.to_csv(sum_csv, index=False)
        with open(sum_json, "w", encoding="utf-8") as f:
            json.dump(summary_df.to_dict(orient="records"), f, indent=2, default=str)

        if extra_metadata is not None:
            with open(meta_json, "w", encoding="utf-8") as f:
                json.dump(extra_metadata, f, indent=2, default=str)

        return {
            "raw_csv": raw_csv,
            "raw_json": raw_json,
            "summary_csv": sum_csv,
            "summary_json": sum_json,
            "metadata_json": meta_json,
        }
