"""Dataset validation and cryptographic range verification utilities for clinical cohorts."""

from typing import Optional
from dataset.schemas import CollaborativeDataset, DISEASE_DIAGNOSIS_STAGES


class DatasetValidator:
    """Validates multi-hospital clinical cohort datasets and plaintext reference calculations."""

    @staticmethod
    def validate_dataset(
        dataset: CollaborativeDataset,
        plaintext_modulus: Optional[int] = None,
    ) -> bool:
        """
        Performs comprehensive invariant checks on a CollaborativeDataset.
        Raises ValueError if any invariant is violated.
        """
        if dataset.total_transactions <= 0:
            raise ValueError("Dataset must contain at least 1 patient record.")

        if len(dataset.partitions) != dataset.num_participants:
            raise ValueError(
                f"Expected {dataset.num_participants} hospital partitions, found {len(dataset.partitions)}"
            )

        observed_count = 0
        observed_sum = 0
        observed_risk = 0
        seen_record_ids: set[str] = set()

        for p_id, part in dataset.partitions.items():
            if len(part.amounts) != len(part.risk_scores):
                raise ValueError(
                    f"Hospital {p_id} has mismatched biomarker_scores ({len(part.amounts)}) and severity_weights ({len(part.risk_scores)})"
                )
            if part.count <= 0:
                raise ValueError(f"Hospital partition {p_id} cannot be empty.")

            for val in part.amounts:
                if not (dataset.amount_min <= val <= dataset.amount_max):
                    raise ValueError(
                        f"Biomarker score {val} outside bounds [{dataset.amount_min}, {dataset.amount_max}]"
                    )

            for wt in part.risk_scores:
                if not (dataset.risk_score_min <= wt <= dataset.risk_score_max):
                    raise ValueError(
                        f"Severity weight {wt} outside bounds [{dataset.risk_score_min}, {dataset.risk_score_max}]"
                    )

            if part.transactions:
                if len(part.transactions) != part.count:
                    raise ValueError(
                        f"Hospital {p_id} patient record length mismatch."
                    )
                for rec in part.transactions:
                    if rec.record_id in seen_record_ids:
                        raise ValueError(f"Duplicate patient record ID: {rec.record_id}")
                    seen_record_ids.add(rec.record_id)
                    if rec.diagnosis_stage not in DISEASE_DIAGNOSIS_STAGES:
                        raise ValueError(f"Invalid diagnosis stage: {rec.diagnosis_stage}")

            p_sum = sum(part.amounts)
            p_risk = sum(x * w for x, w in zip(part.amounts, part.risk_scores))
            if dataset.reference.participant_sums.get(p_id) != p_sum:
                raise ValueError(f"Reference sum mismatch for hospital {p_id}")
            if dataset.reference.participant_risk_totals.get(p_id) != p_risk:
                raise ValueError(f"Reference risk_total mismatch for hospital {p_id}")

            observed_count += part.count
            observed_sum += p_sum
            observed_risk += p_risk

        if observed_count != dataset.total_transactions:
            raise ValueError(
                f"Sum of hospital cohort counts ({observed_count}) != total_patients ({dataset.total_transactions})"
            )

        if observed_sum != dataset.reference.total_sum:
            raise ValueError(
                f"Aggregated biomarker sum ({observed_sum}) != reference total_sum ({dataset.reference.total_sum})"
            )

        if observed_risk != dataset.reference.risk_total:
            raise ValueError(
                f"Aggregated severity risk ({observed_risk}) != reference risk_total ({dataset.reference.risk_total})"
            )

        expected_avg = float(observed_sum) / float(observed_count)
        if abs(expected_avg - dataset.reference.exact_average) > 1e-12:
            raise ValueError("Reference exact_average mismatch.")

        if plaintext_modulus is not None:
            half_mod = plaintext_modulus // 2
            if dataset.reference.total_sum >= half_mod:
                raise ValueError(
                    f"total_sum ({dataset.reference.total_sum}) exceeds signed BFV plaintext modulus bound ({half_mod})"
                )
            if dataset.reference.risk_total >= half_mod:
                raise ValueError(
                    f"risk_total ({dataset.reference.risk_total}) exceeds signed BFV plaintext modulus bound ({half_mod})"
                )

        return True
