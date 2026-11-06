from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sys

from automation.adapters.dry_run import DryRunAds, DryRunXae
from automation.core.graph import default_plan, topological_order
from automation.core.models import ProjectSpec
from automation.security.audit import AuditLog
from automation.security.lock import RunLock, RunnerBusy
from automation.services.runner import Runner


def _add_spec_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name", default="minimal", help="project name")
    parser.add_argument(
        "--source",
        default="templates/minimal/Minimal.sln",
        help="source solution used by the real XAE adapter",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        help="run workspace (default: runtime/runs/<timestamp>-<name>)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="report output directory (default: the run workspace)",
    )
    parser.add_argument("--plc-project", default="PLC1", help="PLC project name")
    parser.add_argument("--symbol", default="MAIN.bReady", help="ADS symbol to read")
    parser.add_argument("--ads-type", default="BOOL", help="ADS value type, e.g. BOOL/LREAL")
    parser.add_argument(
        "--expect",
        default="true",
        help="expected ADS value; use 'none' to skip the assertion",
    )
    parser.add_argument("--netid", default="127.0.0.1.1.1", help="local simulation AMS NetId")
    parser.add_argument("--ads-port", type=int, default=851, help="PLC ADS port")
    parser.add_argument("--platform", default="TwinCAT OS (x64)", help="TwinCAT target platform")
    parser.add_argument("--configuration", default="Release", help="build configuration")


def _parse_expected(raw: str) -> object | None:
    value = raw.strip()
    lowered = value.casefold()
    if lowered in {"none", "null"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _spec(args: argparse.Namespace) -> ProjectSpec:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    workspace = (
        Path(args.workspace)
        if args.workspace
        else Path("runtime") / "runs" / f"{stamp}-{args.name}"
    )
    output = Path(args.output) if args.output else workspace
    return ProjectSpec(
        name=args.name,
        source_solution=Path(args.source),
        workspace=workspace,
        output_dir=output,
        plc_project=args.plc_project,
        ads_symbol=args.symbol,
        ads_type=args.ads_type,
        expected_ads_value=_parse_expected(args.expect),
        ads_netid=args.netid,
        ads_port=args.ads_port,
        target_platform=args.platform,
        build_configuration=args.configuration,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tcauto", description="TwinCAT automation runner")
    sub = parser.add_subparsers(dest="command", required=True)

    plan_parser = sub.add_parser("plan", help="show the task DAG")
    _add_spec_args(plan_parser)

    run_parser = sub.add_parser("run", help="run the task DAG")
    _add_spec_args(run_parser)
    run_parser.add_argument(
        "--real",
        action="store_true",
        help="use the real XAE/ADS adapters for a local simulation run",
    )

    args = parser.parse_args(argv)
    spec = _spec(args)
    plan = default_plan(spec)

    if args.command == "plan":
        print(f"project: {spec.name}")
        for index, task in enumerate(topological_order(plan.tasks), start=1):
            deps = ",".join(task.depends_on) or "-"
            print(f"{index}. {task.id:<10} {task.kind.value:<20} deps={deps:<10} resource={task.resource}")
        print(f"workspace: {spec.workspace}")
        print(f"output:    {spec.output_dir}")
        return 0

    if not args.real:
        xae = DryRunXae()
        ads = DryRunAds()
    else:
        from automation.adapters.ads_pyads import PyadsAdapter
        from automation.adapters.xae_com import XaeDteAdapter

        xae = XaeDteAdapter()
        ads = PyadsAdapter(spec.ads_netid, spec.ads_port)

    try:
        with RunLock():
            report = Runner(xae, ads, audit=AuditLog()).run(plan)
    except RunnerBusy as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False, default=str))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
