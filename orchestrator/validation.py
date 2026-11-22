"""Cross-Pipeline Numerical Equivalence and Trust-Assumption Equivalence Validator."""

from typing import Any, Dict
from dataset.schemas import CollaborativeDataset


class CrossEngineValidator:
    """
    Validates that FHE and SMPC pipelines compute identical outputs against the plaintext reference
    and explicitly labels whether the cryptographic trust assumptions are directly equivalent.
    """

    @staticmethod
    def determine_trust_equivalence(
        fhe_result: Dict[str, Any],
        smpc_result: Dict[str, Any],
    ) -> str:
        fhe_mode = str(fhe_result.get("security_model", ""))
        smpc_proto = str(smpc_result.get("protocol", ""))

        if fhe_mode == "threshold_multiparty" and "dishonest_majority" in smpc_proto:
            return (
                "DIRECTLY_EQUIVALENT: Both operate in the 3-party dishonest-majority "
                "semi-honest threat model (tolerating up to t <= 2 of 3 colluding parties)."
            )
        if fhe_mode == "single_key_baseline":
            return (
                "NON_EQUIVALENT_TRUST_MODEL: Single-key FHE baseline requires a designated "
                "key-holder/decryptor and is NOT equivalent to 3-party threshold FHE or SMPC."
            )
        if "honest_majority" in smpc_proto:
            return (
                "DIFFERENT_CORRUPTION_THRESHOLD: 3-party Threshold FHE tolerates dishonest "
                "majority (t <= 2 of 3), whereas Shamir SMPC requires honest majority (t <= 1 of 3)."
            )
        if "malicious" in smpc_proto:
            return (
                "DIFFERENT_ADVERSARY_MODEL: SMPC enforces active malicious security with abort, "
                "whereas Threshold BFV operates under passive/noise-flooding security."
            )
        return "COMPARABLE_WITH_DOCUMENTED_ASSUMPTIONS"

    @classmethod
    def validate_pair(
        cls,
        dataset: CollaborativeDataset,
        workload: str,
        fhe_result: Dict[str, Any],
        smpc_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Asserts that FHE, SMPC, and the PlaintextReferenceResult produce identical outputs.
        """
        if workload == "aggregation":
            ref_val = dataset.reference.total_sum
        elif workload == "risk_multiplication":
            ref_val = dataset.reference.risk_total
        else:
            raise ValueError(f"Unknown workload '{workload}'")

        fhe_val = int(fhe_result["computed_value"])
        smpc_val = int(smpc_result["computed_value"])

        if fhe_val != ref_val:
            raise AssertionError(
                f"FHE result ({fhe_val}) != plaintext reference ({ref_val}) for {workload}"
            )
        if smpc_val != ref_val:
            raise AssertionError(
                f"SMPC result ({smpc_val}) != plaintext reference ({ref_val}) for {workload}"
            )
        if fhe_val != smpc_val:
            raise AssertionError(
                f"FHE ({fhe_val}) and SMPC ({smpc_val}) results diverged on {workload}!"
            )

        if workload == "aggregation":
            fhe_avg = float(fhe_result["computed_average"])
            smpc_avg = float(smpc_result["computed_average"])
            ref_avg = float(dataset.reference.exact_average)
            if abs(fhe_avg - smpc_avg) > 1e-9 or abs(fhe_avg - ref_avg) > 1e-9:
                raise AssertionError(
                    f"Average mismatch: FHE={fhe_avg}, SMPC={smpc_avg}, Ref={ref_avg}"
                )

        trust_label = cls.determine_trust_equivalence(fhe_result, smpc_result)
        fhe_result["trust_comparison_label"] = trust_label
        smpc_result["trust_comparison_label"] = trust_label
        fhe_result["cross_engine_verified"] = True
        smpc_result["cross_engine_verified"] = True

        return {
            "workload": workload,
            "num_transactions": dataset.total_transactions,
            "reference_value": ref_val,
            "fhe_value": fhe_val,
            "smpc_value": smpc_val,
            "exact_match": True,
            "trust_comparison_label": trust_label,
        }
