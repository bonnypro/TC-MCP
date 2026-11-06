from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = WORKSPACE_ROOT / "config" / "tool_profiles.toml"


@dataclass(frozen=True)
class ToolProfile:
    name: str
    description: str
    tools: tuple[str, ...]

    def allows(self, tool: str) -> bool:
        return "*" in self.tools or tool in self.tools


def load_profiles(path: Path | None = None) -> dict[str, ToolProfile]:
    source = (path or DEFAULT_PATH).resolve()
    with source.open("rb") as handle:
        data = tomllib.load(handle)
    result: dict[str, ToolProfile] = {}
    for name, raw in data.get("profiles", {}).items():
        tools = tuple(str(item) for item in raw.get("tools", ()))
        if not tools:
            raise ValueError(f"profile {name!r} has no tools")
        result[name] = ToolProfile(
            name=name,
            description=str(raw.get("description", "")),
            tools=tools,
        )
    if "tc-minimal" not in result:
        raise ValueError("tool_profiles.toml must define tc-minimal")
    return result
