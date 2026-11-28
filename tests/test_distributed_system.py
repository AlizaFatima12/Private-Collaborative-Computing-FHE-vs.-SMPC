"""Integration tests for the Real 4-Node Distributed Collaborative Computing System."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import urllib.request
import pytest

from system.coordinator import DistributedCoordinator
from system.node_agent import ClientNodeRuntime, create_node_handler
from system.web_app import create_coordinator_handler


def _start_test_cluster(tmp_path: Path):
    """Spins up 3 isolated HTTP Node Agents + 1 Untrusted Coordinator in separate dirs/ports."""
    node_servers = []
    node_urls = []
    for idx in range(3):
        node_dir = tmp_path / f"node_{idx + 1}"
        runtime = ClientNodeRuntime(
            node_index=idx,
            data_dir=node_dir,
            organization=f"Org {idx + 1}",
            tag=f"Zone-{idx + 1}",
        )
        runtime.net_interface = "lo"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), create_node_handler(runtime))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        node_servers.append(srv)
        node_urls.append(f"http://127.0.0.1:{srv.server_address[1]}")

    coord_dir = tmp_path / "coordinator"
    coord = DistributedCoordinator(node_urls=node_urls, data_dir=coord_dir)
    coord.net_interface = "lo"
    return node_servers, node_urls, coord, coord_dir


def test_distributed_schema_alignment_and_isolation():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        node_servers, _, coord, coord_dir = _start_test_cluster(tmp_path)
        try:
            coord.proxy_upload_to_node(0, "id,val,wt,grp\n1,10,2,A\n2,20,3,B\n", "c1.csv")
            coord.proxy_upload_to_node(1, "id,val,wt,grp\n3,30,4,A\n4,40,5,B\n", "c2.csv")
            coord.proxy_upload_to_node(2, "id,other,wt,grp\n5,50,6,A\n", "bad.csv")

            bad_status = coord.get_cluster_status()
            assert bad_status["reachable_nodes"] == 3
            assert bad_status["all_datasets_loaded"] is True
            assert bad_status["schema_aligned"] is False
            assert "Schema mismatch" in bad_status["schema_error"]

            # Fix node 3 schema
            coord.proxy_upload_to_node(2, "id,val,wt,grp\n5,50,6,A\n", "c3.csv")
            good_status = coord.get_cluster_status()
            assert good_status["schema_aligned"] is True
            assert good_status["total_rows"] == 5
            assert "val" in good_status["shared_numeric_columns"]
            assert "grp" in good_status["shared_categorical_columns"]
            # Coordinator must never persist raw CSV files
            assert not (coord_dir / "dataset.csv").exists()
        finally:
            for srv in node_servers:
                srv.shutdown()
                srv.server_close()


def test_distributed_fhe_and_smpc_all_workloads():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        node_servers, _, coord, _ = _start_test_cluster(tmp_path)
        try:
            coord.proxy_upload_to_node(0, "id,metric,weight,cat\n1,12.5,2,ALPHA\n2,17.5,4,BETA\n", "n1.csv")
            coord.proxy_upload_to_node(1, "id,metric,weight,cat\n3,20.0,3,ALPHA\n4,10.0,1,BETA\n", "n2.csv")
            coord.proxy_upload_to_node(2, "id,metric,weight,cat\n5,15.0,2,ALPHA\n6,25.0,5,BETA\n", "n3.csv")

            # 1. Aggregation
            res_agg = coord.run_job(engine="both", workload="aggregation", value_col="metric")
            assert res_agg["cross_verification"]["exact_match"] is True
            assert abs(res_agg["outputs"]["sum"] - 100.0) < 1e-6
            assert abs(res_agg["outputs"]["mean"] - (100.0 / 6.0)) < 1e-5
            assert res_agg["fhe"]["audit_proof"]["coordinator_saw_raw_rows"] == 0
            assert res_agg["fhe"]["audit_proof"]["coordinator_held_secret_keys"] == 0
            assert res_agg["smpc"]["audit_proof"]["coordinator_saw_raw_rows"] == 0

            # 2. Weighted Inner Product
            res_prod = coord.run_job(
                engine="both",
                workload="risk_multiplication",
                value_col="metric",
                weight_col="weight",
            )
            expected_prod = 12.5 * 2 + 17.5 * 4 + 20.0 * 3 + 10.0 * 1 + 15.0 * 2 + 25.0 * 5
            assert res_prod["cross_verification"]["exact_match"] is True
            assert abs(res_prod["outputs"]["weighted_inner_product"] - expected_prod) < 1e-6

            # 3. Global Variance
            res_var = coord.run_job(engine="both", workload="variance", value_col="metric")
            vals = [12.5, 17.5, 20.0, 10.0, 15.0, 25.0]
            mean = sum(vals) / len(vals)
            expected_var = sum((v - mean) ** 2 for v in vals) / len(vals)
            assert res_var["cross_verification"]["exact_match"] is True
            assert abs(res_var["outputs"]["variance"] - expected_var) < 1e-4

            # 4. Private Filtered Aggregation (cat == ALPHA -> 12.5 + 20.0 + 15.0 = 47.5, count = 3)
            res_filt = coord.run_job(
                engine="both",
                workload="filtered_aggregation",
                value_col="metric",
                filter_col="cat",
                filter_val="ALPHA",
            )
            assert res_filt["cross_verification"]["exact_match"] is True
            assert abs(res_filt["outputs"]["sum"] - 47.5) < 1e-6
            assert res_filt["outputs"]["matched_count"] == 3
        finally:
            for srv in node_servers:
                srv.shutdown()
                srv.server_close()


def test_distributed_web_api_endpoints():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        node_servers, _, coord, _ = _start_test_cluster(tmp_path)
        web_srv = ThreadingHTTPServer(("127.0.0.1", 0), create_coordinator_handler(coord))
        threading.Thread(target=web_srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{web_srv.server_address[1]}"

        try:
            for idx, csv_body in enumerate([
                "id,x\n1,10\n2,20\n",
                "id,x\n3,30\n4,40\n",
                "id,x\n5,50\n",
            ]):
                req = urllib.request.Request(
                    f"{base}/api/node/upload",
                    data=json.dumps({"node_index": idx, "csv_text": csv_body, "filename": f"n{idx}.csv"}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    assert json.loads(resp.read())["ok"] is True

            with urllib.request.urlopen(f"{base}/api/cluster", timeout=10) as resp:
                cluster = json.loads(resp.read())
                assert cluster["schema_aligned"] is True
                assert cluster["total_rows"] == 5

            job_req = urllib.request.Request(
                f"{base}/api/jobs/run",
                data=json.dumps({"engine": "fhe", "workload": "aggregation", "value_col": "x"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(job_req, timeout=60) as resp:
                job_out = json.loads(resp.read())
                assert abs(job_out["outputs"]["sum"] - 150.0) < 1e-6
        finally:
            web_srv.shutdown()
            web_srv.server_close()
            for srv in node_servers:
                srv.shutdown()
                srv.server_close()


def test_dynamic_add_and_remove_organizations():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        node_servers, _, coord, _ = _start_test_cluster(tmp_path)
        try:
            # Add Organization 4 & Organization 5 (auto-provisioned isolated nodes)
            coord.add_organization("Organization 4", "Site-4")
            coord.add_organization("Organization 5", "Site-5")
            st5 = coord.get_cluster_status()
            assert st5["node_count"] == 5
            assert st5["reachable_nodes"] == 5

            for idx, val in enumerate([10, 20, 30, 40, 50]):
                coord.proxy_upload_to_node(
                    idx,
                    f"id,x,w\n{idx + 1},{val},{idx + 1}\n",
                    f"org{idx + 1}.csv",
                )

            res5 = coord.run_job(
                engine="both",
                workload="risk_multiplication",
                value_col="x",
                weight_col="w",
            )
            assert res5["node_count"] == 5
            assert res5["cross_verification"]["exact_match"] is True
            # 10*1 + 20*2 + 30*3 + 40*4 + 50*5 = 10 + 40 + 90 + 160 + 250 = 550
            assert abs(res5["outputs"]["weighted_inner_product"] - 550.0) < 1e-6

            # Remove Organization 4 -> drops to 4 nodes and re-indexes cleanly
            coord.remove_organization(3)
            st4 = coord.get_cluster_status()
            assert st4["node_count"] == 4
            res4 = coord.run_job(engine="both", workload="aggregation", value_col="x")
            assert res4["node_count"] == 4
            assert res4["cross_verification"]["exact_match"] is True
            # Remaining: 10 + 20 + 30 + 50 = 110
            assert abs(res4["outputs"]["sum"] - 110.0) < 1e-6
        finally:
            coord.shutdown_managed_nodes()
            for srv in node_servers:
                srv.shutdown()
                srv.server_close()
