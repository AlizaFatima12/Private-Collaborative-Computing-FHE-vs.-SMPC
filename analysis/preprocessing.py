"""Benchmark Result Loading, Cleaning, and Multi-Dimensional Slice Extraction."""

import json
from pathlib import Path
from typing import Any, Dict, Optional
import pandas as pd


class BenchmarkDataPreprocessor:
    """Loads raw and summarized benchmark CSVs and extracts comparable experimental slices."""

    def __init__(self, results_dir: Optional[str | Path] = None):
        if results_dir is not None:
            self.results_dir = Path(results_dir)
        else:
            self.results_dir = Path(__file__).resolve().parent.parent / "results"

        self.raw_csv = self.results_dir / "raw" / "raw_benchmarks.csv"
        self.summary_csv = self.results_dir / "processed" / "summary_benchmarks.csv"
        self.metadata_json = self.results_dir / "processed" / "experiment_metadata.json"

    def load_raw_measured(self) -> pd.DataFrame:
        if not self.raw_csv.exists():
            raise FileNotFoundError(f"Raw benchmark CSV not found: {self.raw_csv}")
        df = pd.read_csv(self.raw_csv)
        if "is_warmup" in df.columns:
            df = df[~df["is_warmup"].astype(bool)].copy()
        return df

    def load_summary(self) -> pd.DataFrame:
        if not self.summary_csv.exists():
            raise FileNotFoundError(f"Summary benchmark CSV not found: {self.summary_csv}")
        return pd.read_csv(self.summary_csv)

    def load_metadata(self) -> Dict[str, Any]:
        if not self.metadata_json.exists():
            return {}
        with open(self.metadata_json, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def filter_primary_protocols(df: pd.DataFrame) -> pd.DataFrame:
        """
        Filters for the primary directly-comparable 3-party dishonest-majority protocols:
          - FHE: threshold_bfv_128
          - SMPC: semi_dishonest_majority_3p
        """
        if df.empty or "protocol" not in df.columns:
            return df
        mask = df["protocol"].isin(["threshold_bfv_128", "semi_dishonest_majority_3p"])
        filtered = df[mask].copy()
        return filtered if not filtered.empty else df.copy()

    def get_dataset_scaling_slice(
        self,
        workload: str = "risk_multiplication",
        latency_profile: str = "local",
        bandwidth_profile: str = "high",
    ) -> pd.DataFrame:
        df = self.filter_primary_protocols(self.load_summary())
        sub = df[
            (df["workload"] == workload)
            & (df["latency_profile"] == latency_profile)
            & (df["bandwidth_profile"] == bandwidth_profile)
        ].copy()
        return sub.sort_values(["technology", "num_transactions"])

    def get_latency_sweep_slice(
        self,
        workload: str = "risk_multiplication",
        dataset_profile: str = "medium",
        bandwidth_profile: str = "high",
    ) -> pd.DataFrame:
        df = self.filter_primary_protocols(self.load_summary())
        sub = df[
            (df["workload"] == workload)
            & (df["dataset_profile"] == dataset_profile)
            & (df["bandwidth_profile"] == bandwidth_profile)
        ].copy()
        return sub.sort_values(["technology", "configured_latency_ms"])

    def get_bandwidth_sweep_slice(
        self,
        workload: str = "risk_multiplication",
        dataset_profile: str = "medium",
        latency_profile: str = "local",
    ) -> pd.DataFrame:
        df = self.filter_primary_protocols(self.load_summary())
        sub = df[
            (df["workload"] == workload)
            & (df["dataset_profile"] == dataset_profile)
            & (df["latency_profile"] == latency_profile)
        ].copy()
        return sub.sort_values(["technology", "configured_bandwidth_mbps"])
