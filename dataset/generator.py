"""Deterministic Multi-Region Hospital Disease Cohort Generator and CSV Loader."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
import numpy as np

from dataset.schemas import (
    DISEASE_DIAGNOSIS_STAGES,
    HOSPITAL_REGIONS,
    PatientRecord,
    ParticipantPartition,
    CollaborativeDataset,
    PlaintextReferenceResult,
)


DEFAULT_PARTICIPANTS = [
    {
        "id": "participant_a",
        "index": 0,
        "organization": "Hospital Alpha (Northern Mountain Region)",
        "region": "NORTHERN_MOUNTAIN",
    },
    {
        "id": "participant_b",
        "index": 1,
        "organization": "Hospital Beta (Coastal Maritime Region)",
        "region": "COASTAL_MARITIME",
    },
    {
        "id": "participant_c",
        "index": 2,
        "organization": "Hospital Gamma (Metro Urban Center)",
        "region": "METRO_URBAN",
    },
]

DEFAULT_PROFILES = {
    "small": 100,
    "medium": 1000,
    "large": 10000,
    "very_large": 50000,
    "stress_test": 100000,
}

# Region-specific clinical distributions so each hospital represents a distinct regional cohort
# while staying strictly within [biomarker_min, biomarker_max] and [severity_min, severity_max].
REGIONAL_DISTRIBUTION_PARAMS = {
    "NORTHERN_MOUNTAIN": {"biomarker_mean": 78.0, "biomarker_std": 28.0, "severity_mean": 4.2, "severity_std": 2.0},
    "COASTAL_MARITIME": {"biomarker_mean": 105.0, "biomarker_std": 32.0, "severity_mean": 5.4, "severity_std": 2.2},
    "METRO_URBAN": {"biomarker_mean": 132.0, "biomarker_std": 35.0, "severity_mean": 6.8, "severity_std": 2.3},
}


class DatasetGenerator:
    """Generates deterministic, region-diverse multi-hospital clinical cohorts or loads local hospital CSVs."""

    def __init__(self, config_path: Optional[str | Path] = None):
        self.config_Data: Dict[str, Any] = {}
        if config_path is not None and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                self.config_Data = yaml.safe_load(f) or {}
        else:
            default_cfg = Path(__file__).resolve().parent.parent / "config" / "experiment.yaml"
            if default_cfg.exists():
                with open(default_cfg, "r", encoding="utf-8") as f:
                    self.config_Data = yaml.safe_load(f) or {}

        exp_cfg = self.config_Data.get("experiment", {})
        ds_cfg = self.config_Data.get("datasets", {})

        self.default_seed: int = int(exp_cfg.get("random_seed", 42))
        self.participants_meta: List[Dict[str, Any]] = exp_cfg.get(
            "participants", DEFAULT_PARTICIPANTS
        )
        self.profiles: Dict[str, int] = ds_cfg.get("profiles", dict(DEFAULT_PROFILES))
        self.amount_min: int = int(ds_cfg.get("biomarker_min", ds_cfg.get("amount_min", 10)))
        self.amount_max: int = int(ds_cfg.get("biomarker_max", ds_cfg.get("amount_max", 200)))
        self.risk_score_min: int = int(ds_cfg.get("severity_min", ds_cfg.get("risk_score_min", 1)))
        self.risk_score_max: int = int(ds_cfg.get("severity_max", ds_cfg.get("risk_score_max", 10)))

    def resolve_size(self, profile_or_size: str | int) -> tuple[str, int]:
        """Resolves either a profile name ('small', 'large', etc.) or an integer patient count."""
        if isinstance(profile_or_size, int):
            for name, sz in self.profiles.items():
                if sz == profile_or_size:
                    return name, sz
            return f"custom_{profile_or_size}", profile_or_size

        key = str(profile_or_size).strip().lower()
        if key in self.profiles:
            return key, int(self.profiles[key])
        if key.isdigit():
            val = int(key)
            return f"custom_{val}", val
        raise ValueError(
            f"Unknown dataset profile '{profile_or_size}'. Valid profiles: {list(self.profiles.keys())}"
        )

    @staticmethod
    def _stage_from_severity(sev: int, sev_min: int, sev_max: int) -> str:
        span = max(1, sev_max - sev_min)
        norm = float(sev - sev_min) / float(span)
        idx = min(len(DISEASE_DIAGNOSIS_STAGES) - 1, int(norm * len(DISEASE_DIAGNOSIS_STAGES)))
        return DISEASE_DIAGNOSIS_STAGES[idx]

    def generate(
        self,
        profile_or_size: str | int = "small",
        seed: Optional[int] = None,
        materialize_records: bool = True,
    ) -> CollaborativeDataset:
        """
        Generates a deterministic 3-hospital disease cohort dataset with regional demographic diversity.

        Args:
            profile_or_size: Dataset profile name ('small', 'medium', 'large', 'very_large', 'stress_test')
                             or explicit total patient record count.
            seed: Deterministic PRNG seed (defaults to experiment.yaml random_seed).
            materialize_records: Whether to instantiate PatientRecord objects for every row.
        """
        profile_name, total_n = self.resolve_size(profile_or_size)
        if total_n <= 0:
            raise ValueError(f"total_patients must be positive; got {total_n}")

        active_seed = self.default_seed if seed is None else int(seed)
        num_parties = len(self.participants_meta)
        if total_n < num_parties:
            raise ValueError(
                f"total_patients ({total_n}) must be >= num_hospitals ({num_parties})"
            )

        rng = np.random.Generator(np.random.PCG64(active_seed))
        base_epoch = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        base_chunk = total_n // num_parties
        remainder = total_n % num_parties

        partitions: Dict[str, ParticipantPartition] = {}
        participant_sums: Dict[str, int] = {}
        participant_counts: Dict[str, int] = {}
        participant_risk_totals: Dict[str, int] = {}

        cursor = 0
        for p_idx, p_meta in enumerate(self.participants_meta):
            p_id = str(p_meta["id"])
            p_org = str(p_meta.get("organization", p_id))
            p_region = str(
                p_meta.get("region", HOSPITAL_REGIONS[p_idx % len(HOSPITAL_REGIONS)])
            )
            p_size = base_chunk + (1 if p_idx < remainder else 0)
            start_i = cursor
            cursor += p_size

            dist = REGIONAL_DISTRIBUTION_PARAMS.get(p_region)
            if dist is not None:
                bio_raw = np.rint(
                    rng.normal(dist["biomarker_mean"], dist["biomarker_std"], size=p_size)
                ).astype(np.int64)
                bio_arr = np.clip(bio_raw, self.amount_min, self.amount_max)

                sev_raw = np.rint(
                    rng.normal(dist["severity_mean"], dist["severity_std"], size=p_size)
                ).astype(np.int64)
                sev_arr = np.clip(sev_raw, self.risk_score_min, self.risk_score_max)
            else:
                bio_arr = rng.integers(
                    self.amount_min, self.amount_max + 1, size=p_size, dtype=np.int64
                )
                sev_arr = rng.integers(
                    self.risk_score_min, self.risk_score_max + 1, size=p_size, dtype=np.int64
                )

            p_amounts = [int(x) for x in bio_arr.tolist()]
            p_risks = [int(w) for w in sev_arr.tolist()]

            records: List[PatientRecord] = []
            if materialize_records:
                patient_codes = rng.integers(10000, 99999, size=p_size, dtype=np.int64).tolist()
                time_offsets = rng.integers(0, 86400 * 30, size=p_size, dtype=np.int64).tolist()
                for local_k in range(p_size):
                    global_k = start_i + local_k
                    ts_str = (
                        base_epoch + timedelta(seconds=int(time_offsets[local_k]))
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    sev_val = p_risks[local_k]
                    records.append(
                        PatientRecord(
                            record_id=f"PAT-{global_k + 1:06d}",
                            patient_code=f"PT-{int(patient_codes[local_k])}",
                            hospital_id=p_id,
                            region=p_region,
                            biomarker_score=p_amounts[local_k],
                            diagnosis_stage=self._stage_from_severity(
                                sev_val, self.risk_score_min, self.risk_score_max
                            ),
                            severity_weight=sev_val,
                            recorded_at=ts_str,
                        )
                    )

            partitions[p_id] = ParticipantPartition(
                participant_id=p_id,
                participant_index=p_idx,
                organization=p_org,
                region=p_region,
                transactions=records,
                amounts=p_amounts,
                risk_scores=p_risks,
            )

            p_sum = int(sum(p_amounts))
            p_risk_total = int(sum(x * w for x, w in zip(p_amounts, p_risks)))
            participant_sums[p_id] = p_sum
            participant_counts[p_id] = p_size
            participant_risk_totals[p_id] = p_risk_total

        total_sum = int(sum(participant_sums.values()))
        total_count = int(sum(participant_counts.values()))
        exact_average = float(total_sum) / float(total_count)
        integer_average = total_sum // total_count
        risk_total = int(sum(participant_risk_totals.values()))

        reference = PlaintextReferenceResult(
            total_sum=total_sum,
            total_count=total_count,
            exact_average=exact_average,
            integer_average=integer_average,
            risk_total=risk_total,
            participant_sums=participant_sums,
            participant_counts=participant_counts,
            participant_risk_totals=participant_risk_totals,
        )

        return CollaborativeDataset(
            profile_name=profile_name,
            total_transactions=total_n,
            random_seed=active_seed,
            num_participants=num_parties,
            partitions=partitions,
            reference=reference,
            amount_min=self.amount_min,
            amount_max=self.amount_max,
            risk_score_min=self.risk_score_min,
            risk_score_max=self.risk_score_max,
        )

    def from_csv_files(
        self,
        hospital_csvs: Dict[str, str | Path],
        profile_name: str = "custom_hospital_csv",
        biomarker_col: str = "biomarker_score",
        severity_col: str = "severity_weight",
    ) -> CollaborativeDataset:
        """
        Loads private patient cohorts from 3 separate hospital CSV files without pooling raw files
        into a single table prior to cryptographic partitioning.
        """
        if len(hospital_csvs) != len(self.participants_meta):
            raise ValueError(
                f"Expected {len(self.participants_meta)} hospital CSVs, got {len(hospital_csvs)}"
            )

        partitions: Dict[str, ParticipantPartition] = {}
        participant_sums: Dict[str, int] = {}
        participant_counts: Dict[str, int] = {}
        participant_risk_totals: Dict[str, int] = {}
        all_bio: List[int] = []
        all_sev: List[int] = []
        global_idx = 0

        for p_idx, p_meta in enumerate(self.participants_meta):
            p_id = str(p_meta["id"])
            if p_id not in hospital_csvs:
                raise KeyError(f"Missing CSV path for hospital '{p_id}'")
            csv_path = Path(hospital_csvs[p_id])
            if not csv_path.exists():
                raise FileNotFoundError(f"Hospital CSV not found: {csv_path}")

            p_org = str(p_meta.get("organization", p_id))
            p_region = str(
                p_meta.get("region", HOSPITAL_REGIONS[p_idx % len(HOSPITAL_REGIONS)])
            )

            p_amounts: List[int] = []
            p_risks: List[int] = []
            records: List[PatientRecord] = []

            with open(csv_path, "r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    global_idx += 1
                    bio_val = int(round(float(row[biomarker_col])))
                    sev_val = int(round(float(row[severity_col])))
                    stage = str(row.get("diagnosis_stage", "")).strip()
                    if stage not in DISEASE_DIAGNOSIS_STAGES:
                        stage = self._stage_from_severity(
                            sev_val, self.risk_score_min, self.risk_score_max
                        )
                    rec_id = str(row.get("record_id") or f"PAT-{global_idx:06d}")
                    pt_code = str(row.get("patient_code") or f"PT-{10000 + global_idx}")
                    rec_region = str(row.get("region") or p_region)
                    rec_ts = str(row.get("recorded_at") or "2026-01-01T00:00:00Z")

                    p_amounts.append(bio_val)
                    p_risks.append(sev_val)
                    all_bio.append(bio_val)
                    all_sev.append(sev_val)
                    records.append(
                        PatientRecord(
                            record_id=rec_id,
                            patient_code=pt_code,
                            hospital_id=p_id,
                            region=rec_region,
                            biomarker_score=bio_val,
                            diagnosis_stage=stage,
                            severity_weight=sev_val,
                            recorded_at=rec_ts,
                        )
                    )

            partitions[p_id] = ParticipantPartition(
                participant_id=p_id,
                participant_index=p_idx,
                organization=p_org,
                region=p_region,
                transactions=records,
                amounts=p_amounts,
                risk_scores=p_risks,
            )
            p_sum = int(sum(p_amounts))
            p_risk_total = int(sum(x * w for x, w in zip(p_amounts, p_risks)))
            participant_sums[p_id] = p_sum
            participant_counts[p_id] = len(p_amounts)
            participant_risk_totals[p_id] = p_risk_total

        total_n = int(sum(participant_counts.values()))
        total_sum = int(sum(participant_sums.values()))
        exact_average = float(total_sum) / float(total_n)
        integer_average = total_sum // total_n
        risk_total = int(sum(participant_risk_totals.values()))

        reference = PlaintextReferenceResult(
            total_sum=total_sum,
            total_count=total_n,
            exact_average=exact_average,
            integer_average=integer_average,
            risk_total=risk_total,
            participant_sums=participant_sums,
            participant_counts=participant_counts,
            participant_risk_totals=participant_risk_totals,
        )

        return CollaborativeDataset(
            profile_name=profile_name,
            total_transactions=total_n,
            random_seed=self.default_seed,
            num_participants=len(self.participants_meta),
            partitions=partitions,
            reference=reference,
            amount_min=min(self.amount_min, min(all_bio)),
            amount_max=max(self.amount_max, max(all_bio)),
            risk_score_min=min(self.risk_score_min, min(all_sev)),
            risk_score_max=max(self.risk_score_max, max(all_sev)),
        )
