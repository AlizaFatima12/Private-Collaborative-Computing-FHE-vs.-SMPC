"""Data schemas for multi-hospital clinical cohorts, hospital partitions, and reference results."""

from dataclasses import dataclass, field
from typing import Dict, List, Any


DISEASE_DIAGNOSIS_STAGES = (
    "ASYMPTOMATIC",
    "MILD_OUTPATIENT",
    "MODERATE_WARD",
    "SEVERE_ICU",
    "CRITICAL_VENTILATED",
)

HOSPITAL_REGIONS = (
    "NORTHERN_MOUNTAIN",
    "COASTAL_MARITIME",
    "METRO_URBAN",
)

# Backward-compatible alias
TRANSACTION_CATEGORIES = DISEASE_DIAGNOSIS_STAGES


@dataclass(frozen=True)
class PatientRecord:
    """Single pseudonymized patient clinical record owned by one regional hospital."""

    record_id: str
    patient_code: str
    hospital_id: str
    region: str
    biomarker_score: int
    diagnosis_stage: str
    severity_weight: int
    recorded_at: str

    # Backward-compatible attribute aliases for shared benchmark tooling
    @property
    def transaction_id(self) -> str:
        return self.record_id

    @property
    def account_id(self) -> str:
        return self.patient_code

    @property
    def participant_id(self) -> str:
        return self.hospital_id

    @property
    def amount(self) -> int:
        return self.biomarker_score

    @property
    def category(self) -> str:
        return self.diagnosis_stage

    @property
    def risk_score(self) -> int:
        return self.severity_weight

    @property
    def timestamp(self) -> str:
        return self.recorded_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "record_id": self.record_id,
            "patient_code": self.patient_code,
            "hospital_id": self.hospital_id,
            "region": self.region,
            "biomarker_score": self.biomarker_score,
            "diagnosis_stage": self.diagnosis_stage,
            "severity_weight": self.severity_weight,
            "recorded_at": self.recorded_at,
        }


# Backward-compatible alias
TransactionRecord = PatientRecord


@dataclass(frozen=True)
class PlaintextReferenceResult:
    """Exact ground-truth reference outputs for both multi-hospital clinical workloads."""

    total_sum: int
    total_count: int
    exact_average: float
    integer_average: int
    risk_total: int
    participant_sums: Dict[str, int]
    participant_counts: Dict[str, int]
    participant_risk_totals: Dict[str, int]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_sum": self.total_sum,
            "total_count": self.total_count,
            "exact_average": self.exact_average,
            "integer_average": self.integer_average,
            "risk_total": self.risk_total,
            "participant_sums": dict(self.participant_sums),
            "participant_counts": dict(self.participant_counts),
            "participant_risk_totals": dict(self.participant_risk_totals),
        }


@dataclass
class ParticipantPartition:
    """Private clinical cohort partition belonging to a single regional hospital."""

    participant_id: str
    participant_index: int
    organization: str
    region: str = "UNKNOWN"
    transactions: List[PatientRecord] = field(default_factory=list)
    amounts: List[int] = field(default_factory=list)
    risk_scores: List[int] = field(default_factory=list)

    @property
    def patient_records(self) -> List[PatientRecord]:
        return self.transactions

    @property
    def biomarker_scores(self) -> List[int]:
        return self.amounts

    @property
    def severity_weights(self) -> List[int]:
        return self.risk_scores

    @property
    def count(self) -> int:
        return len(self.amounts)

    @property
    def raw_plaintext_bytes(self) -> int:
        """Each integer clinical input stored as 64-bit (8-byte) integer."""
        return len(self.amounts) * 8

    @property
    def dual_vector_plaintext_bytes(self) -> int:
        """Both biomarker_score and severity_weight vectors stored as 64-bit (8-byte) integers."""
        return (len(self.amounts) + len(self.risk_scores)) * 8


@dataclass
class CollaborativeDataset:
    """Complete 3-hospital partitioned clinical dataset with metadata and reference results."""

    profile_name: str
    total_transactions: int
    random_seed: int
    num_participants: int
    partitions: Dict[str, ParticipantPartition]
    reference: PlaintextReferenceResult
    amount_min: int
    amount_max: int
    risk_score_min: int
    risk_score_max: int

    @property
    def total_patients(self) -> int:
        return self.total_transactions

    @property
    def biomarker_min(self) -> int:
        return self.amount_min

    @property
    def biomarker_max(self) -> int:
        return self.amount_max

    @property
    def severity_min(self) -> int:
        return self.risk_score_min

    @property
    def severity_max(self) -> int:
        return self.risk_score_max

    @property
    def total_amounts_plaintext_bytes(self) -> int:
        """Total raw plaintext size in bytes for Workload 1 (1 x 64-bit integer per patient)."""
        return self.total_transactions * 8

    @property
    def total_risk_plaintext_bytes(self) -> int:
        """Total raw plaintext size in bytes for Workload 2 (2 x 64-bit integers per patient)."""
        return self.total_transactions * 16

    def get_ordered_partitions(self) -> List[ParticipantPartition]:
        return sorted(self.partitions.values(), key=lambda p: p.participant_index)
