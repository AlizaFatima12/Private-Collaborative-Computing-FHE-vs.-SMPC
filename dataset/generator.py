"""Deterministic Synthetic Financial Transaction Dataset Generator."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml
import numpy as np

from dataset.schemas import (
    TRANSACTION_CATEGORIES,
    TransactionRecord,
    ParticipantPartition,
    CollaborativeDataset,
    PlaintextReferenceResult,
)


DEFAULT_PARTICIPANTS = [
    {"id": "participant_a", "index": 0, "organization": "Bank Alpha (Retail Banking)"},
    {"id": "participant_b", "index": 1, "organization": "Bank Beta (Commercial Treasury)"},
    {"id": "participant_c", "index": 2, "organization": "Bank Gamma (Cross-Border Settlement)"},
]

DEFAULT_PROFILES = {
    "small": 100,
    "medium": 1000,
    "large": 10000,
    "very_large": 50000,
    "stress_test": 100000,
}


class DatasetGenerator:
    """Generates deterministic, synthetic, multi-party financial transaction datasets."""

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
        self.amount_min: int = int(ds_cfg.get("amount_min", 10))
        self.amount_max: int = int(ds_cfg.get("amount_max", 200))
        self.risk_score_min: int = int(ds_cfg.get("risk_score_min", 1))
        self.risk_score_max: int = int(ds_cfg.get("risk_score_max", 10))

    def resolve_size(self, profile_or_size: str | int) -> tuple[str, int]:
        """Resolves either a profile name ('small', 'large', etc.) or an integer size."""
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

    def generate(
        self,
        profile_or_size: str | int = "small",
        seed: Optional[int] = None,
        materialize_records: bool = True,
    ) -> CollaborativeDataset:
        """
        Generates a deterministic 3-party financial transaction dataset.

        Args:
            profile_or_size: Dataset profile name ('small', 'medium', 'large', 'very_large', 'stress_test')
                             or explicit transaction count.
            seed: Deterministic PRNG seed (defaults to experiment.yaml random_seed).
            materialize_records: Whether to instantiate TransactionRecord objects for every row.
        """
        profile_name, total_n = self.resolve_size(profile_or_size)
        if total_n <= 0:
            raise ValueError(f"total_transactions must be positive; got {total_n}")

        active_seed = self.default_seed if seed is None else int(seed)
        num_parties = len(self.participants_meta)
        if total_n < num_parties:
            raise ValueError(
                f"total_transactions ({total_n}) must be >= num_participants ({num_parties})"
            )

        rng = np.random.Generator(np.random.PCG64(active_seed))

        # Generate deterministic arrays for all N transactions
        amounts_arr = rng.integers(
            self.amount_min, self.amount_max + 1, size=total_n, dtype=np.int64
        )
        risk_arr = rng.integers(
            self.risk_score_min, self.risk_score_max + 1, size=total_n, dtype=np.int64
        )
        cat_indices = rng.integers(
            0, len(TRANSACTION_CATEGORIES), size=total_n, dtype=np.int64
        )
        acct_numbers = rng.integers(10000, 99999, size=total_n, dtype=np.int64)
        time_offsets_sec = rng.integers(0, 86400 * 30, size=total_n, dtype=np.int64)

        base_epoch = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

        # Balanced deterministic partitioning across the 3 organizations
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
            p_size = base_chunk + (1 if p_idx < remainder else 0)
            start_i = cursor
            end_i = cursor + p_size
            cursor = end_i

            p_amounts = [int(x) for x in amounts_arr[start_i:end_i].tolist()]
            p_risks = [int(w) for w in risk_arr[start_i:end_i].tolist()]

            records: List[TransactionRecord] = []
            if materialize_records:
                p_cats = cat_indices[start_i:end_i].tolist()
                p_accts = acct_numbers[start_i:end_i].tolist()
                p_times = time_offsets_sec[start_i:end_i].tolist()
                for local_k in range(p_size):
                    global_k = start_i + local_k
                    ts_str = (
                        base_epoch + timedelta(seconds=int(p_times[local_k]))
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                    records.append(
                        TransactionRecord(
                            transaction_id=f"TX-{global_k + 1:06d}",
                            account_id=f"ACC-{int(p_accts[local_k])}",
                            participant_id=p_id,
                            amount=p_amounts[local_k],
                            category=TRANSACTION_CATEGORIES[int(p_cats[local_k])],
                            risk_score=p_risks[local_k],
                            timestamp=ts_str,
                        )
                    )

            partitions[p_id] = ParticipantPartition(
                participant_id=p_id,
                participant_index=p_idx,
                organization=p_org,
                transactions=records,
                amounts=p_amounts,
                risk_scores=p_risks,
            )

            p_sum = int(sum(p_amounts))
            p_risk_total = int(
                sum(x * w for x, w in zip(p_amounts, p_risks))
            )
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
