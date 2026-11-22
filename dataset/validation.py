"""Dataset validation and cryptographic range verification utilities."""

from typing import Optional
from dataset.schemas import CollaborativeDataset, TRANSACTION_CATEGORIES


class DatasetValidator:
    """Validates synthetic financial transaction datasets and plaintext reference calculations."""

    @staticmethod
    def validate_dataset(
        dataset: CollaborativeDataset,
        plaintext_modulus: Optional[int] = None,
    ) -> bool:
        """
        Performs comprehensive invariant checks on a generated CollaborativeDataset.
        Raises ValueError if any invariant is violated.
        """
        if dataset.total_transactions <= 0:
            raise ValueError("Dataset must contain at least 1 transaction.")

        if len(dataset.partitions) != dataset.num_participants:
            raise ValueError(
                f"Expected {dataset.num_participants} partitions, found {len(dataset.partitions)}"
            )

        observed_count = 0
        observed_sum = 0
        observed_risk = 0
        seen_tx_ids: set[str] = set()

        for p_id, part in dataset.partitions.items():
            if len(part.amounts) != len(part.risk_scores):
                raise ValueError(
                    f"Partition {p_id} has mismatched amounts ({len(part.amounts)}) and risk_scores ({len(part.risk_scores)})"
                )
            if part.count <= 0:
                raise ValueError(f"Partition {p_id} cannot be empty.")

            for amt in part.amounts:
                if not (dataset.amount_min <= amt <= dataset.amount_max):
                    raise ValueError(
                        f"Transaction amount {amt} outside bounds [{dataset.amount_min}, {dataset.amount_max}]"
                    )

            for rsk in part.risk_scores:
                if not (dataset.risk_score_min <= rsk <= dataset.risk_score_max):
                    raise ValueError(
                        f"Risk score {rsk} outside bounds [{dataset.risk_score_min}, {dataset.risk_score_max}]"
                    )

            if part.transactions:
                if len(part.transactions) != part.count:
                    raise ValueError(
                        f"Partition {p_id} transaction records length mismatch."
                    )
                for rec in part.transactions:
                    if rec.transaction_id in seen_tx_ids:
                        raise ValueError(f"Duplicate transaction ID: {rec.transaction_id}")
                    seen_tx_ids.add(rec.transaction_id)
                    if rec.category not in TRANSACTION_CATEGORIES:
                        raise ValueError(f"Invalid transaction category: {rec.category}")

            p_sum = sum(part.amounts)
            p_risk = sum(x * w for x, w in zip(part.amounts, part.risk_scores))
            if dataset.reference.participant_sums.get(p_id) != p_sum:
                raise ValueError(f"Reference sum mismatch for participant {p_id}")
            if dataset.reference.participant_risk_totals.get(p_id) != p_risk:
                raise ValueError(f"Reference risk_total mismatch for participant {p_id}")

            observed_count += part.count
            observed_sum += p_sum
            observed_risk += p_risk

        if observed_count != dataset.total_transactions:
            raise ValueError(
                f"Sum of partition counts ({observed_count}) != total_transactions ({dataset.total_transactions})"
            )

        if observed_sum != dataset.reference.total_sum:
            raise ValueError(
                f"Aggregated sum ({observed_sum}) != reference total_sum ({dataset.reference.total_sum})"
            )

        if observed_risk != dataset.reference.risk_total:
            raise ValueError(
                f"Aggregated risk ({observed_risk}) != reference risk_total ({dataset.reference.risk_total})"
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
