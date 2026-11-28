"""CLI for the Distributed N-Node Private Collaborative Computing System."""

import argparse
import json
from pathlib import Path
import sys

from system.coordinator import DistributedCoordinator


def _sample_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "sample_datasets"


def main() -> None:
    parser = argparse.ArgumentParser(description="CLI for Distributed Collaborative Computing System")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check cluster connectivity and node schema alignment.")

    add_p = sub.add_parser("add-org", help="Add a new organization node (auto-provisioned or remote URL).")
    add_p.add_argument("--org", required=True, help="Organization name")
    add_p.add_argument("--tag", default="", help="Location / tag")
    add_p.add_argument("--url", default=None, help="Optional remote node URL (leave blank to auto-provision)")

    rm_p = sub.add_parser("remove-org", help="Remove an organization by 1-based node number.")
    rm_p.add_argument("--node", type=int, required=True, help="Node number (1, 2, 3, ...)")

    up = sub.add_parser("upload", help="Upload a CSV file directly to one Client Node.")
    up.add_argument("--node", type=int, required=True, help="Node number (1, 2, 3, ...)")
    up.add_argument("--csv", required=True, help="Path to local CSV file")
    up.add_argument("--org", default=None, help="Organization name")
    up.add_argument("--tag", default=None, help="Node tag/location")

    run_p = sub.add_parser("run", help="Submit a distributed collaborative job.")
    run_p.add_argument("--engine", default="fhe", choices=["fhe", "smpc", "both"])
    run_p.add_argument("--workload", default="aggregation", choices=["aggregation", "risk_multiplication", "variance", "filtered_aggregation"])
    run_p.add_argument("--value-col", default="")
    run_p.add_argument("--weight-col", default=None)
    run_p.add_argument("--filter-col", default=None)
    run_p.add_argument("--filter-val", default=None)
    run_p.add_argument("--fhe-profile", default="threshold_bfv_128")
    run_p.add_argument("--smpc-protocol", default="semi_dishonest_majority_3p")
    run_p.add_argument("--apply-network", action="store_true")
    run_p.add_argument("--latency", default="local")
    run_p.add_argument("--bandwidth", default="high")

    sub.add_parser("verify-e2e", help="Load sample_datasets into all active nodes and verify FHE + SMPC.")

    args = parser.parse_args()
    coord = DistributedCoordinator()

    if args.command == "health":
        print(json.dumps(coord.get_cluster_status(), indent=2))
        return

    if args.command == "add-org":
        print(json.dumps(coord.add_organization(args.org, args.tag, args.url), indent=2))
        return

    if args.command == "remove-org":
        print(json.dumps(coord.remove_organization(args.node - 1), indent=2))
        return

    if args.command == "upload":
        csv_path = Path(args.csv)
        if not csv_path.exists():
            print(f"CSV file not found: {csv_path}", file=sys.stderr)
            sys.exit(1)
        res = coord.proxy_upload_to_node(
            node_index=args.node - 1,
            csv_text=csv_path.read_text(encoding="utf-8"),
            filename=csv_path.name,
            organization=args.org,
            tag=args.tag,
        )
        print(json.dumps(res, indent=2))
        return

    if args.command == "run":
        res = coord.run_job(
            engine=args.engine,
            workload=args.workload,
            value_col=args.value_col,
            weight_col=args.weight_col,
            filter_col=args.filter_col,
            filter_val=args.filter_val,
            fhe_profile=args.fhe_profile,
            smpc_protocol=args.smpc_protocol,
            latency_profile=args.latency,
            bandwidth_profile=args.bandwidth,
            apply_network=args.apply_network,
        )
        print(json.dumps(res, indent=2))
        return

    if args.command == "verify-e2e":
        sdir = _sample_dir()
        st = coord.get_cluster_status()
        for idx in range(st["node_count"]):
            csv_file = sdir / f"client_{(idx % 3) + 1}.csv"
            coord.proxy_upload_to_node(
                node_index=idx,
                csv_text=csv_file.read_text(encoding="utf-8"),
                filename=f"org_{idx + 1}_{csv_file.name}",
                organization=f"Organization {idx + 1}",
                tag=f"Site-{idx + 1}",
            )
        for wl in ("aggregation", "risk_multiplication", "variance", "filtered_aggregation"):
            extra = {"filter_col": "category", "filter_val": "GROUP_A"} if wl == "filtered_aggregation" else {}
            res = coord.run_job(
                engine="both",
                workload=wl,
                value_col="primary_metric",
                weight_col="weight_factor" if wl == "risk_multiplication" else None,
                **extra,
            )
            matched = res["cross_verification"]["exact_match"]
            print(f"[verify-e2e] {wl} ({res['node_count']} nodes): exact_match={matched} | outputs={res['outputs']}")
            if not matched:
                sys.exit(1)
        print(f"All 4 distributed workloads verified across {st['node_count']} nodes!")


if __name__ == "__main__":
    main()
