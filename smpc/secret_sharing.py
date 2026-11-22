"""SMPC Secret Sharing Storage Expansion and Share Representation Inspector."""

from dataclasses import dataclass
from typing import Any, Dict
from dataset.schemas import CollaborativeDataset
from smpc.protocol_config import SMPCProtocolSpec


@dataclass(frozen=True)
class SecretSharingStorageMetrics:
    """Quantifies secret share sizes across the 3 participants relative to raw plaintext."""

    plaintext_bytes_total: int
    bytes_per_field_element: int
    shares_per_element_per_party: int
    total_share_bytes_all_parties: int
    share_bytes_per_party: int
    share_expansion_ratio_all_parties: float
    share_expansion_ratio_per_party: float

    def to_metrics_dict(self) -> Dict[str, Any]:
        return {
            "smpc_plaintext_bytes_total": self.plaintext_bytes_total,
            "smpc_bytes_per_field_element": self.bytes_per_field_element,
            "smpc_total_share_bytes_all_parties": self.total_share_bytes_all_parties,
            "smpc_share_bytes_per_party": self.share_bytes_per_party,
            "smpc_share_expansion_ratio_all_parties": round(
                self.share_expansion_ratio_all_parties, 4
            ),
            "smpc_share_expansion_ratio_per_party": round(
                self.share_expansion_ratio_per_party, 4
            ),
        }


class SecretSharingInspector:
    """Computes cryptographic share storage requirements for 3-party MP-SPDZ secret sharing schemes."""

    @staticmethod
    def compute_storage_metrics(
        dataset: CollaborativeDataset,
        spec: SMPCProtocolSpec,
        workload: str = "aggregation",
    ) -> SecretSharingStorageMetrics:
        """
        Calculates exact in-memory / network secret-share representation size for the dataset.
        In MP-SPDZ prime-field protocols (-F 64), the prime modulus p is 128-bit (16 bytes per field element).
        - Passive additive (semi) and Shamir (shamir / mal-shamir): 1 field element (16 bytes) per party per secret.
        - Authenticated SPDZ (mascot): 2 field elements (16-byte share + 16-byte MAC share = 32 bytes) per party per secret.
        """
        num_elements = (
            dataset.total_transactions * 2
            if workload == "risk_multiplication"
            else dataset.total_transactions
        )
        plaintext_bytes = (
            dataset.total_risk_plaintext_bytes
            if workload == "risk_multiplication"
            else dataset.total_amounts_plaintext_bytes
        )

        # MP-SPDZ -F 64 uses a 128-bit prime field (16 bytes per field element) for statistical security
        bytes_per_field_elem = 16

        if "mascot" in spec.binary_online or "spdz" in spec.binary_online:
            # Share + Information-Theoretic MAC share per party
            components_per_party = 2
        elif "replicated" in spec.binary_online:
            # Replicated secret sharing: each of 3 parties holds 2 of 3 additive shares
            components_per_party = 2
        else:
            # Standard additive (semi) or Shamir (shamir / malicious-shamir): 1 field element per party
            components_per_party = 1

        share_bytes_per_party = num_elements * bytes_per_field_elem * components_per_party
        total_share_bytes = share_bytes_per_party * spec.num_parties

        ratio_all = (
            float(total_share_bytes) / float(plaintext_bytes)
            if plaintext_bytes > 0
            else 0.0
        )
        ratio_party = (
            float(share_bytes_per_party) / float(plaintext_bytes)
            if plaintext_bytes > 0
            else 0.0
        )

        return SecretSharingStorageMetrics(
            plaintext_bytes_total=plaintext_bytes,
            bytes_per_field_element=bytes_per_field_elem,
            shares_per_element_per_party=components_per_party,
            total_share_bytes_all_parties=total_share_bytes,
            share_bytes_per_party=share_bytes_per_party,
            share_expansion_ratio_all_parties=ratio_all,
            share_expansion_ratio_per_party=ratio_party,
        )
