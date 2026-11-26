"""Built-in Web GUI Server for 3-Hospital Collaborative Disease Research Demo."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import traceback
from urllib.parse import unquote

from demo.runner import (
    inspect_dynamic_csvs,
    list_demo_options,
    load_crossover_highlights,
    load_decision_matrix,
    preview_hospital_cohorts,
    run_dynamic_demo,
    run_dynamic_sweep,
    run_hospital_demo,
)


ROOT_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
RESULTS_DIR = ROOT_DIR / "results"
_RUN_LOCK = threading.Lock()


class HospitalDemoHandler(BaseHTTPRequestHandler):
    """Serves the interactive HTML dashboard, live FHE vs. SMPC API endpoints, and report artifacts."""

    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args) -> None:
        # Keep container logs concise
        sys_msg = format % args
        print(f"[GUI] {self.address_string()} - {sys_msg}", flush=True)

    def _send_json(self, payload: dict, status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, file_path: Path) -> None:
        resolved = file_path.resolve()
        if not resolved.exists() or not resolved.is_file():
            self._send_json({"error": f"File not found: {file_path.name}"}, HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(resolved))
        data = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
            return
        if path == "/api/config":
            self._send_json(list_demo_options())
            return
        if path == "/api/decision-matrix":
            self._send_json({
                "rows": load_decision_matrix(),
                "crossovers": load_crossover_highlights(),
            })
            return
        if path.startswith("/results/graphs/"):
            rel = path[len("/results/"):]
            target = (RESULTS_DIR / rel).resolve()
            if not str(target).startswith(str(RESULTS_DIR.resolve())):
                self._send_json({"error": "Forbidden path"}, HTTPStatus.FORBIDDEN)
                return
            self._send_file(target)
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = unquote(self.path.split("?", 1)[0])
        try:
            body = self._read_json_body()
        except Exception as exc:
            self._send_json({"error": f"Invalid JSON request: {exc}"}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/inspect-csvs":
            try:
                out = inspect_dynamic_csvs(clients=body.get("clients", []))
                self._send_json(out)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/run-dynamic":
            with _RUN_LOCK:
                try:
                    out = run_dynamic_demo(
                        clients=body.get("clients", []),
                        value_col=body.get("value_col", ""),
                        weight_col=body.get("weight_col") or None,
                        workload=body.get("workload", "risk_multiplication"),
                        latency_profile=body.get("latency_profile", "local"),
                        bandwidth_profile=body.get("bandwidth_profile", "high"),
                        fhe_profile=body.get("fhe_profile", "threshold_bfv_128"),
                        smpc_protocol=body.get("smpc_protocol", "semi_dishonest_majority_3p"),
                        apply_network=bool(body.get("apply_network", True)),
                        scale_mode=str(body.get("scale_mode", "auto")),
                    )
                    self._send_json(out)
                except Exception as exc:
                    traceback.print_exc()
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/run-sweep":
            with _RUN_LOCK:
                try:
                    out = run_dynamic_sweep(
                        clients=body.get("clients", []),
                        value_col=body.get("value_col", ""),
                        weight_col=body.get("weight_col") or None,
                        workload=body.get("workload", "risk_multiplication"),
                        fhe_profile=body.get("fhe_profile", "threshold_bfv_128"),
                        smpc_protocol=body.get("smpc_protocol", "semi_dishonest_majority_3p"),
                        apply_network=bool(body.get("apply_network", True)),
                        scale_mode=str(body.get("scale_mode", "auto")),
                    )
                    self._send_json(out)
                except Exception as exc:
                    traceback.print_exc()
                    self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/preview-cohorts":
            try:
                out = preview_hospital_cohorts(
                    size=body.get("size", "small"),
                    seed=int(body.get("seed", 42)),
                    custom_csvs=body.get("custom_csvs") or None,
                )
                self._send_json(out)
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return

        if path == "/api/run-demo":
            with _RUN_LOCK:
                try:
                    out = run_hospital_demo(
                        size=body.get("size", "small"),
                        workload=body.get("workload", "risk_multiplication"),
                        latency_profile=body.get("latency_profile", "local"),
                        bandwidth_profile=body.get("bandwidth_profile", "high"),
                        fhe_profile=body.get("fhe_profile", "threshold_bfv_128"),
                        smpc_protocol=body.get("smpc_protocol", "semi_dishonest_majority_3p"),
                        seed=int(body.get("seed", 42)),
                        apply_network=bool(body.get("apply_network", True)),
                        custom_csvs=body.get("custom_csvs") or None,
                    )
                    self._send_json(out)
                except Exception as exc:
                    traceback.print_exc()
                    self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start 3-Hospital FHE vs. SMPC Web GUI")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8501)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), HospitalDemoHandler)
    print(f"Hospital FHE vs. SMPC Demo GUI running at http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
