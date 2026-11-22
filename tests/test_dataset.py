"""Unit tests for deterministic synthetic financial dataset generation and schema validation."""

import pytest
from dataset import DatasetGenerator, DatasetValidator, TRANSACTION_CATEGORIES


def test_dataset_profiles_and_counts():
    gen = DatasetGenerator()
    for profile, expected_n in [
        ("small", 100),
        ("medium", 1000),
        ("large", 10000),
        ("very_large", 50000),
        ("stress_test", 100000),
    ]:
        materialize = expected_n <= 10000
        ds = gen.generate(profile, seed=42, materialize_records=materialize)
        assert ds.profile_name == profile
        assert ds.total_transactions == expected_n
        assert ds.num_participants == 3
        assert len(ds.partitions) == 3
        assert sum(p.count for p in ds.partitions.values()) == expected_n
        assert DatasetValidator.validate_dataset(ds, plaintext_modulus=536903681)


def test_deterministic_seed_reproducibility():
    gen = DatasetGenerator()
    ds1 = gen.generate("small", seed=12345)
    ds2 = gen.generate("small", seed=12345)
    ds3 = gen.generate("small", seed=99999)

    assert ds1.reference.total_sum == ds2.reference.total_sum
    assert ds1.reference.risk_total == ds2.reference.risk_total
    for p_id in ("participant_a", "participant_b", "participant_c"):
        assert ds1.partitions[p_id].amounts == ds2.partitions[p_id].amounts
        assert ds1.partitions[p_id].risk_scores == ds2.partitions[p_id].risk_scores

    # Different seed produces distinct synthetic transactions
    assert (
        ds1.reference.total_sum != ds3.reference.total_sum
        or ds1.reference.risk_total != ds3.reference.risk_total
    )


def test_transaction_record_fields_and_partition_disjointness():
    gen = DatasetGenerator()
    ds = gen.generate("small", seed=42, materialize_records=True)

    all_tx_ids = set()
    for p_id, part in ds.partitions.items():
        assert len(part.transactions) == part.count
        for rec in part.transactions:
            assert rec.participant_id == p_id
            assert rec.transaction_id.startswith("TX-")
            assert rec.account_id.startswith("ACC-")
            assert ds.amount_min <= rec.amount <= ds.amount_max
            assert ds.risk_score_min <= rec.risk_score <= ds.risk_score_max
            assert rec.category in TRANSACTION_CATEGORIES
            assert rec.timestamp.endswith("Z")
            assert rec.transaction_id not in all_tx_ids
            all_tx_ids.add(rec.transaction_id)

    assert len(all_tx_ids) == 100


def test_modulus_overflow_detection():
    gen = DatasetGenerator()
    ds = gen.generate("medium", seed=42)
    with pytest.raises(ValueError, match="exceeds signed BFV plaintext modulus bound"):
        DatasetValidator.validate_dataset(ds, plaintext_modulus=101)
