"""Tests for the 3-Hospital CLI and Web GUI Demonstration Engine."""

import json
import subprocess
import sys
import pytest
from demo.runner import (
    inspect_dynamic_csvs,
    list_demo_options,
    load_crossover_highlights,
    load_decision_matrix,
    preview_hospital_cohorts,
    run_dynamic_demo,
    run_hospital_demo,
)


def test_demo_options_and_preview():
    opts = list_demo_options()
    assert len(opts["participants"]) == 3
    assert "aggregation" in opts["workloads"]
    assert "risk_multiplication" in opts["workloads"]
    assert "ieee_pdf_available" not in opts

    dm = load_decision_matrix()
    assert isinstance(dm, list)
    cxs = load_crossover_highlights()
    assert isinstance(cxs, list)

    prev = preview_hospital_cohorts(size="small", seed=42)
    assert prev["total_patients"] == 100
    assert len(prev["hospitals"]) == 3
    assert all("Hospital" in h["organization"] for h in prev["hospitals"])
    assert prev["hospitals"][0]["stage_counts"]


def test_run_hospital_demo_custom_csv():
    custom_csvs = {
        "participant_a": "patient_code,biomarker_score,severity_weight\nPT-1,50,2\nPT-2,70,3\n",
        "participant_b": "patient_code,biomarker_score,severity_weight\nPT-3,100,5\nPT-4,110,6\n",
        "participant_c": "patient_code,biomarker_score,severity_weight\nPT-5,150,8\nPT-6,170,9\n",
    }
    res = run_hospital_demo(
        workload="risk_multiplication",
        apply_network=False,
        custom_csvs=custom_csvs,
    )
    assert res["validation"]["exact_match"] is True
    expected = 50 * 2 + 70 * 3 + 100 * 5 + 110 * 6 + 150 * 8 + 170 * 9
    assert res["fhe"]["computed_value"] == expected
    assert res["smpc"]["computed_value"] == expected


def test_cli_json_mode():
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "demo.cli",
            "--size",
            "small",
            "--workload",
            "aggregation",
            "--no-netem",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    json_start = proc.stdout.find("{")
    assert json_start != -1
    payload = json.loads(proc.stdout[json_start:])
    assert payload["validation"]["exact_match"] is True
    assert payload["cohort_summary"]["total_patients"] == 100


def test_dynamic_3client_csv_validation_and_execution():
    valid_clients = [
        {"name": "Alpha Lab", "tag": "North", "csv_text": "id,score,weight\n1,12.5,2\n2,17.5,4\n"},
        {"name": "Beta Corp", "tag": "East", "csv_text": "id,score,weight\n3,20.0,3\n4,10.0,1\n"},
        {"name": "Gamma Org", "tag": "West", "csv_text": "id,score,weight\n5,15.0,2\n6,25.0,5\n"},
    ]
    info = inspect_dynamic_csvs(valid_clients)
    assert info["columns"] == ["id", "score", "weight"]
    assert "score" in info["numeric_columns"]
    assert "weight" in info["numeric_columns"]
    assert info["total_rows"] == 6

    # Mismatched columns must fail with clear error
    bad_clients = [
        valid_clients[0],
        {"name": "Beta Corp", "tag": "East", "csv_text": "id,different_col,weight\n3,20,3\n"},
        valid_clients[2],
    ]
    with pytest.raises(ValueError, match="Column mismatch"):
        inspect_dynamic_csvs(bad_clients)

    # Decimal scaling + exact cross-engine match
    res = run_dynamic_demo(
        clients=valid_clients,
        value_col="score",
        weight_col="weight",
        workload="risk_multiplication",
        apply_network=False,
    )
    assert res["validation"]["exact_match"] is True
    expected_product = 12.5 * 2 + 17.5 * 4 + 20.0 * 3 + 10.0 * 1 + 15.0 * 2 + 25.0 * 5
    assert abs(res["dataset_meta"]["unscaled_weighted_product"] - expected_product) < 1e-6
