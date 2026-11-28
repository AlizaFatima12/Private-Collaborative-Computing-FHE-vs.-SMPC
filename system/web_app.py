"""Coordinator Web Portal & Distributed Collaborative Computing API Server."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import threading
import traceback
from urllib.parse import unquote
import urllib.error
import urllib.request
import yaml

from system.coordinator import DistributedCoordinator


ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
CONFIG_DIR = ROOT_DIR / "config"
SAMPLES_DIR = ROOT_DIR / "sample_datasets"
_JOB_LOCK = threading.Lock()


def _load_options() -> dict:
    with open(CONFIG_DIR / "fhe.yaml", "r", encoding="utf-8") as f:
        fhe_cfg = yaml.safe_load(f) or {}
    with open(CONFIG_DIR / "smpc.yaml", "r", encoding="utf-8") as f:
        smpc_cfg = yaml.safe_load(f) or {}
    with open(CONFIG_DIR / "network.yaml", "r", encoding="utf-8") as f:
        net_cfg = yaml.safe_load(f) or {}

    return {
        "engines": {
            "fhe": "OpenFHE (Distributed Threshold BFV)",
            "smpc": "MP-SPDZ (Distributed Secret Sharing)",
            "both": "Both (Cross-Verify FHE + SMPC)",
        },
        "workloads": {
            "aggregation": "Aggregation (Sum, Count & Mean)",
            "risk_multiplication": "Weighted Inner Product (Sum of Value * Weight)",
            "variance": "Global Variance & Std Dev",
            "filtered_aggregation": "Private Filtered Aggregation (Where Column == Value)",
        },
        "fhe_profiles": [
            k for k, v in fhe_cfg.get("fhe", {}).get("profiles", {}).items()
            if v.get("mode") == "threshold_multiparty"
        ],
        "smpc_protocols": list(smpc_cfg.get("smpc", {}).get("protocols", {}).keys()),
        "latency_profiles": {
            k: f"{v.get('label', k)} ({v.get('one_way_delay_ms')} ms)"
            for k, v in net_cfg.get("network", {}).get("latency_profiles", {}).items()
        },
        "bandwidth_profiles": {
            k: str(v.get("label", k))
            for k, v in net_cfg.get("network", {}).get("bandwidth_profiles", {}).items()
        },
    }


def create_coordinator_handler(coordinator: DistributedCoordinator):
    class CoordinatorHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args) -> None:
            print(f"[Coordinator] {self.address_string()} - {format % args}", flush=True)

        def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_file(self, file_path: Path) -> None:
            if not file_path.exists():
                self._send_json({"error": "File not found"}, HTTPStatus.NOT_FOUND)
                return
            mime, _ = mimetypes.guess_type(str(file_path))
            data = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mime or "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _read_body_bytes(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0:
                return b""
            return self.rfile.read(length)

        def _read_json(self) -> dict:
            raw = self._read_body_bytes()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))

        def _proxy_node_portal(self, method: str, path: str) -> bool:
            prefix = "/node-portal/"
            if not path.startswith(prefix):
                return False
            rest = path[len(prefix):]
            parts = rest.split("/", 1)
            if not parts[0].isdigit():
                self._send_json({"error": "Invalid node index"}, HTTPStatus.BAD_REQUEST)
                return True
            node_idx = int(parts[0])
            if len(parts) == 1:
                self.send_response(HTTPStatus.FOUND)
                self.send_header("Location", f"/node-portal/{node_idx}/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return True
            sub_path = "/" + parts[1]
            try:
                base_url = coordinator.get_node_internal_url(node_idx).rstrip("/")
                target_url = base_url + sub_path
                body_bytes = self._read_body_bytes() if method == "POST" else None
                headers = {"Content-Type": "application/json"} if body_bytes is not None else {}
                req = urllib.request.Request(target_url, data=body_bytes, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30.0) as resp:
                    resp_bytes = resp.read()
                    ctype = resp.headers.get("Content-Type", "application/octet-stream")
                    self.send_response(resp.status)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(len(resp_bytes)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(resp_bytes)
            except urllib.error.HTTPError as exc:
                err_bytes = exc.read()
                self.send_response(exc.code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(err_bytes)))
                self.end_headers()
                self.wfile.write(err_bytes)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_GATEWAY)
            return True

        def do_GET(self) -> None:
            path = unquote(self.path.split("?", 1)[0])
            if self._proxy_node_portal("GET", path):
                return
            if path in ("/", "/index.html"):
                self._send_file(STATIC_DIR / "index.html")
                return
            if path == "/api/cluster":
                try:
                    cluster = coordinator.get_cluster_status()
                    cluster["options"] = _load_options()
                    self._send_json(cluster)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            if path == "/api/jobs":
                self._send_json({"jobs": coordinator.list_jobs()})
                return
            if path.startswith("/api/sample-csv/"):
                idx_str = path.rsplit("/", 1)[-1]
                if not idx_str.isdigit() or int(idx_str) < 1:
                    self._send_json({"error": "Invalid sample index"}, HTTPStatus.BAD_REQUEST)
                    return
                sample_num = ((int(idx_str) - 1) % 3) + 1
                sample_file = SAMPLES_DIR / f"client_{sample_num}.csv"
                if not sample_file.exists():
                    self._send_json({"error": "Sample CSV not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({
                    "filename": f"org_{idx_str}_{sample_file.name}",
                    "csv_text": sample_file.read_text(encoding="utf-8"),
                })
                return
            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:
            path = unquote(self.path.split("?", 1)[0])
            if self._proxy_node_portal("POST", path):
                return
            try:
                body = self._read_json()
            except Exception as exc:
                self._send_json({"error": f"Invalid JSON: {exc}"}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/organizations/add":
                try:
                    entry = coordinator.add_organization(
                        organization=body.get("organization", ""),
                        tag=body.get("tag", ""),
                        url=body.get("url") or None,
                    )
                    self._send_json({"ok": True, "organization": entry})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/organizations/remove":
                try:
                    res = coordinator.remove_organization(node_index=int(body["node_index"]))
                    self._send_json({"ok": True, **res})
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/node/upload":
                try:
                    res = coordinator.proxy_upload_to_node(
                        node_index=int(body["node_index"]),
                        csv_text=body.get("csv_text", ""),
                        filename=body.get("filename", "dataset.csv"),
                        organization=body.get("organization"),
                        tag=body.get("tag"),
                    )
                    self._send_json(res)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            if path == "/api/jobs/run":
                with _JOB_LOCK:
                    try:
                        res = coordinator.run_job(
                            engine=body.get("engine", "fhe"),
                            workload=body.get("workload", "aggregation"),
                            value_col=body.get("value_col", ""),
                            weight_col=body.get("weight_col") or None,
                            filter_col=body.get("filter_col") or None,
                            filter_val=body.get("filter_val") or None,
                            scale_mode=str(body.get("scale_mode", "auto")),
                            fhe_profile=body.get("fhe_profile", "threshold_bfv_128"),
                            smpc_protocol=body.get("smpc_protocol", "semi_dishonest_majority_3p"),
                            latency_profile=body.get("latency_profile", "local"),
                            bandwidth_profile=body.get("bandwidth_profile", "high"),
                            apply_network=bool(body.get("apply_network", False)),
                        )
                        self._send_json(res)
                    except Exception as exc:
                        traceback.print_exc()
                        self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return

            self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    return CoordinatorHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Start Distributed Coordinator Web Portal")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PCC_COORDINATOR_PORT", "8501")))
    args = parser.parse_args()

    coord = DistributedCoordinator()
    server = ThreadingHTTPServer((args.host, args.port), create_coordinator_handler(coord))
    print(f"Distributed Coordinator Console running on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        coord.shutdown_managed_nodes()
        server.server_close()


if __name__ == "__main__":
    main()
