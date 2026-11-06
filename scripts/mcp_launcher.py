from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation.evolution.config import load_config
from automation.evolution.launcher import LaunchError, LaunchTarget, resolve_launch_target


def main() -> int:
    parser = argparse.ArgumentParser(description="Start tc-auto from a release channel.")
    parser.add_argument("--print", dest="show", action="store_true", help="print the resolved target and exit")
    parser.add_argument("--version-id", default=None, help="override the registry with an explicit version")
    parser.add_argument(
        "--channel",
        default=os.environ.get("TC_MCP_CHANNEL", "stable"),
        choices=("stable", "canary"),
        help="release channel to launch (default: stable)",
    )
    parser.add_argument(
        "--profile",
        default=os.environ.get("TC_MCP_TOOL_PROFILE"),
        help="tool profile exposed by the server (for example tc-minimal)",
    )
    parser.add_argument("server_args", nargs=argparse.REMAINDER, help="arguments passed to automation_mcp_server.py")
    args = parser.parse_args()

    config = load_config()
    requested = args.version_id or os.environ.get("TC_MCP_VERSION") or None
    try:
        if os.environ.get("TC_MCP_DISABLE_EVOLUTION") == "1":
            target = LaunchTarget(
                ROOT, None, "repo-root", "evolution disabled", args.channel
            )
        else:
            target = resolve_launch_target(
                ROOT,
                config.versions_root,
                config.worktrees_root,
                version_id=requested,
                channel=args.channel,
            )
    except LaunchError as exc:
        print(f"tc-auto launcher: {exc}", file=sys.stderr)
        return 2

    if args.show:
        print(json.dumps(target.as_dict(), indent=2, ensure_ascii=False))
        return 0

    if not target.server.is_file():
        print(f"tc-auto launcher: server not found: {target.server}", file=sys.stderr)
        return 2

    from scripts.mcp_lifecycle import install as install_lifecycle

    install_lifecycle()
    command = [sys.executable, str(target.server), *args.server_args]
    env = dict(os.environ)
    if args.profile:
        env["TC_MCP_TOOL_PROFILE"] = str(args.profile)
    try:
        completed = subprocess.run(command, cwd=target.root, env=env)
    except KeyboardInterrupt:
        return 130
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
