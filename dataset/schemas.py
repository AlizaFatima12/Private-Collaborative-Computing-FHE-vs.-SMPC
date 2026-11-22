"""Data schemas for synthetic financial transactions, participant partitions, and reference results."""

from dataclasses import dataclass, field
from typing import Dict, List, Any


TRANSACTION_CATEGORIES = (
    "WIRE_TRANSFER",
    "MERCHANT_POS",
    "ACH_CLEARING",
    "FX_SETTLEMENT",
    "TREASURY_BOND",
)


@dataclass(frozen=True)
class TransactionRecord:
    """Single synthetic financial transaction record owned by one participant."""

    transaction_id: str
    account_id: str
    participant_id: str
    amount: int
    category: str
    risk_score: int
    timestamp: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "account_id": self.account_id,
            "participant_id": self.participant_id,
            "amount": self.amount,
            "category": self.category,
            "risk_score": self.risk_score,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class PlaintextReferenceResult:
    """Exact ground-truth reference outputs for both workloads."""

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
    """Private dataset partition belonging to a single simulated organization."""

    participant_id: str
    participant_index: int
    organization: str
    transactions: List[TransactionRecord] = field(default_factory=list)
    amounts: List[int] = field(default_factory=list)
    risk_scores: List[int] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.amounts)

    @property
    def raw_plaintext_bytes(self) -> int:
        """Each integer input (amount + risk_score) stored as 64-bit (8-byte) integer."""
        return len(self.amounts) * 8

    @property
    def dual_vector_plaintext_bytes(self) -> int:
        """Both amount and risk_score vectors stored as 64-bit (8-byte) integers."""
        return (len(self.amounts) + len(self.risk_scores)) * 8


@dataclass
class CollaborativeDataset:
    """Complete 3-party partitioned dataset with deterministic metadata and reference results."""

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
    def total_amounts_plaintext_bytes(self) -> int:
        """Total raw plaintext size in bytes for Workload 1 (1 x 64-bit integer per transaction)."""
        return self.total_transactions * 8

    @property
    def total_risk_plaintext_bytes(self) -> int:
        """Total raw plaintext size in bytes for Workload 2 (2 x 64-bit integers per transaction: amount & risk_score)."""
        return self.total_transactions * 16

    def get_ordered_partitions(self) -> List[ParticipantPartition]:
        return sorted(self.partitions.values(), key=lambda p: p.participant_index)
