"""Unit tests for deterministic multi-hospital clinical cohort generation, regional diversity, CSV loading, and validation."""

from pathlib import Path
import pytest
from dataset import (
    DatasetGenerator,
    DatasetValidator,
    DISEASE_DIAGNOSIS_STAGES,
    HOSPITAL_REGIONS,
)


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
        assert ds.total_patients == expected_n
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
        assert ds1.partitions[p_id].biomarker_scores == ds2.partitions[p_id].biomarker_scores
        assert ds1.partitions[p_id].severity_weights == ds2.partitions[p_id].severity_weights

    assert (
        ds1.reference.total_sum != ds3.reference.total_sum
        or ds1.reference.risk_total != ds3.reference.risk_total
    )


def test_patient_record_fields_regional_diversity_and_disjointness():
    gen = DatasetGenerator()
    ds = gen.generate("medium", seed=42, materialize_records=True)

    all_record_ids = set()
    hospital_means = {}
    for p_id, part in ds.partitions.items():
        assert part.region in HOSPITAL_REGIONS
        assert "Hospital" in part.organization
        assert len(part.patient_records) == part.count
        hospital_means[p_id] = sum(part.biomarker_scores) / part.count

        for rec in part.patient_records:
            assert rec.hospital_id == p_id
            assert rec.record_id.startswith("PAT-")
            assert rec.patient_code.startswith("PT-")
            assert rec.region == part.region
            assert ds.biomarker_min <= rec.biomarker_score <= ds.biomarker_max
            assert ds.severity_min <= rec.severity_weight <= ds.severity_max
            assert rec.diagnosis_stage in DISEASE_DIAGNOSIS_STAGES
            assert rec.recorded_at.endswith("Z")
            assert rec.record_id not in all_record_ids
            all_record_ids.add(rec.record_id)

    assert len(all_record_ids) == 1000
    # Regional cohorts exhibit distinct clinical baselines (North < Coastal < Metro Urban)
    assert (
        hospital_means["participant_a"]
        < hospital_means["participant_b"]
        < hospital_means["participant_c"]
    )


def test_hospital_csv_ingestion(tmp_path: Path):
    csv_a = tmp_path / "hospital_north.csv"
    csv_b = tmp_path / "hospital_coastal.csv"
    csv_c = tmp_path / "hospital_urban.csv"

    csv_a.write_text(
        "patient_code,biomarker_score,severity_weight,diagnosis_stage\n"
        "PT-101,60,3,MILD_OUTPATIENT\n"
        "PT-102,80,4,MODERATE_WARD\n",
        encoding="utf-8",
    )
    csv_b.write_text(
        "patient_code,biomarker_score,severity_weight,diagnosis_stage\n"
        "PT-201,110,5,MODERATE_WARD\n"
        "PT-202,95,6,SEVERE_ICU\n",
        encoding="utf-8",
    )
    csv_c.write_text(
        "patient_code,biomarker_score,severity_weight,diagnosis_stage\n"
        "PT-301,150,8,SEVERE_ICU\n"
        "PT-302,175,9,CRITICAL_VENTILATED\n",
        encoding="utf-8",
    )

    gen = DatasetGenerator()
    ds = gen.from_csv_files(
        {
            "participant_a": csv_a,
            "participant_b": csv_b,
            "participant_c": csv_c,
        }
    )
    assert ds.total_patients == 6
    assert ds.reference.total_sum == 670
    assert ds.reference.risk_total == (60 * 3 + 80 * 4 + 110 * 5 + 95 * 6 + 150 * 8 + 175 * 9)
    assert DatasetValidator.validate_dataset(ds, plaintext_modulus=536903681)


def test_modulus_overflow_detection():
    gen = DatasetGenerator()
    ds = gen.generate("medium", seed=42)
    with pytest.raises(ValueError, match="exceeds signed BFV plaintext modulus bound"):
        DatasetValidator.validate_dataset(ds, plaintext_modulus=101)
