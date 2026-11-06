from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = WORKSPACE_ROOT / "config" / "automation.toml"


@dataclass(frozen=True)
class DialogRuleConfig:
    id: str
    category: str
    action: str
    title_any: tuple[str, ...] = ()
    body_any: tuple[str, ...] = ()
    buttons: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    max_per_run: int = 0


@dataclass(frozen=True)
class DialogConfig:
    enabled: bool = True
    watchdog_interval_s: float = 10.0
    check_immediately: bool = True
    active_only: bool = True
    operation_timeout_s: float = 30.0
    screenshot: bool = True
    allow_unknown_close: bool = True
    allow_unknown_approve: bool = False
    allow: tuple[DialogRuleConfig, ...] = ()
    deny: tuple[DialogRuleConfig, ...] = ()


@dataclass(frozen=True)
class ResourceConfig:
    max_llm_workers: int = 6
    max_heavy_processes: int = 4
    max_cpu_percent: int = 100
    test_cpu_percent: int = 60
    min_free_memory_gb: int = 4
    min_free_disk_gb: int = 100
    task_timeout_s: int = 300


@dataclass(frozen=True)
class SecurityCommandsConfig:
    allow_prefixes: tuple[str, ...] = ()
    deny_substrings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecurityConfig:
    write_root: Path = WORKSPACE_ROOT / "runtime"
    hardware_allowed: bool = False
    allowed_ads_netids: tuple[str, ...] = ("127.0.0.1.1.1",)
    allowed_ads_ports: tuple[int, ...] = (851,)
    forbidden_name_parts: tuple[str, ...] = ("safety", "twinsafe", "safeplc")
    allow_remote_targets: bool = False
    allow_os_dialogs: bool = False
    allow_safety_project_writes: bool = False
    commands: SecurityCommandsConfig = field(default_factory=SecurityCommandsConfig)


@dataclass(frozen=True)
class SessionConfig:
    template: str = "templates/minimal/Minimal.sln"
    platform: str = "TwinCAT OS (x64)"
    configuration: str = "Release"
    plc_project: str = "PLC1"
    ads_netid: str = "127.0.0.1.1.1"
    ads_port: int = 851
    ads_symbol: str = "MAIN.bReady"
    output_root: Path = WORKSPACE_ROOT / "runtime"
    snapshot_root: Path = WORKSPACE_ROOT / "runtime" / "snapshots"
    archive_root: Path = WORKSPACE_ROOT / "runtime" / "archive"
    report_root: Path = WORKSPACE_ROOT / "runtime" / "reports"


@dataclass(frozen=True)
class AppConfig:
    path: Path
    dialog: DialogConfig
    resources: ResourceConfig
    security: SecurityConfig
    session: SessionConfig


def _strings(value: Any, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if not isinstance(value, list):
        raise ValueError("expected a list of strings")
    return tuple(str(item) for item in value)


def _rule(raw: dict[str, Any]) -> DialogRuleConfig:
    if "id" not in raw or "category" not in raw or "action" not in raw:
        raise ValueError("dialog rule requires id, category, and action")
    return DialogRuleConfig(
        id=str(raw["id"]),
        category=str(raw["category"]),
        action=str(raw["action"]),
        title_any=_strings(raw.get("title_any")),
        body_any=_strings(raw.get("body_any")),
        buttons=_strings(raw.get("buttons")),
        requires=_strings(raw.get("requires")),
        max_per_run=int(raw.get("max_per_run", 0)),
    )


def _path(value: str, root: Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else (root / candidate)


def load_config(path: Path | None = None) -> AppConfig:
    config_path = (path or DEFAULT_CONFIG_PATH).resolve()
    if not config_path.is_file():
        raise FileNotFoundError(f"automation config not found: {config_path}")
    with config_path.open("rb") as handle:
        data = tomllib.load(handle)

    raw_dialog = data.get("dialog", {})
    interval = float(raw_dialog.get("watchdog_interval_s", 10.0))
    timeout = float(raw_dialog.get("operation_timeout_s", 30.0))
    if interval < 1.0 or interval > 600.0:
        raise ValueError("dialog.watchdog_interval_s must be between 1 and 600 seconds")
    if timeout < interval:
        raise ValueError("dialog.operation_timeout_s must be >= watchdog_interval_s")

    dialog = DialogConfig(
        enabled=bool(raw_dialog.get("enabled", True)),
        watchdog_interval_s=interval,
        check_immediately=bool(raw_dialog.get("check_immediately", True)),
        active_only=bool(raw_dialog.get("active_only", True)),
        operation_timeout_s=timeout,
        screenshot=bool(raw_dialog.get("screenshot", True)),
        allow_unknown_close=bool(raw_dialog.get("allow_unknown_close", True)),
        allow_unknown_approve=bool(raw_dialog.get("allow_unknown_approve", False)),
        allow=tuple(_rule(item) for item in raw_dialog.get("allow", [])),
        deny=tuple(_rule(item) for item in raw_dialog.get("deny", [])),
    )

    raw_resources = data.get("resources", {})
    resources = ResourceConfig(
        max_llm_workers=int(raw_resources.get("max_llm_workers", 6)),
        max_heavy_processes=int(raw_resources.get("max_heavy_processes", 4)),
        max_cpu_percent=int(raw_resources.get("max_cpu_percent", 100)),
        test_cpu_percent=int(raw_resources.get("test_cpu_percent", 60)),
        min_free_memory_gb=int(raw_resources.get("min_free_memory_gb", 4)),
        min_free_disk_gb=int(raw_resources.get("min_free_disk_gb", 100)),
        task_timeout_s=int(raw_resources.get("task_timeout_s", 300)),
    )

    raw_security = data.get("security", {})
    raw_commands = raw_security.get("commands", {})
    security = SecurityConfig(
        write_root=_path(str(raw_security.get("write_root", "runtime")), WORKSPACE_ROOT),
        hardware_allowed=bool(raw_security.get("hardware_allowed", False)),
        allowed_ads_netids=_strings(raw_security.get("allowed_ads_netids"), ("127.0.0.1.1.1",)),
        allowed_ads_ports=tuple(int(item) for item in raw_security.get("allowed_ads_ports", [851])),
        forbidden_name_parts=_strings(
            raw_security.get("forbidden_name_parts"), ("safety", "twinsafe", "safeplc")
        ),
        allow_remote_targets=bool(raw_security.get("allow_remote_targets", False)),
        allow_os_dialogs=bool(raw_security.get("allow_os_dialogs", False)),
        allow_safety_project_writes=bool(raw_security.get("allow_safety_project_writes", False)),
        commands=SecurityCommandsConfig(
            allow_prefixes=_strings(raw_commands.get("allow_prefixes")),
            deny_substrings=_strings(raw_commands.get("deny_substrings")),
        ),
    )

    raw_session = data.get("session", {})
    session = SessionConfig(
        template=str(raw_session.get("template", "templates/minimal/Minimal.sln")),
        platform=str(raw_session.get("platform", "TwinCAT OS (x64)")),
        configuration=str(raw_session.get("configuration", "Release")),
        plc_project=str(raw_session.get("plc_project", "PLC1")),
        ads_netid=str(raw_session.get("ads_netid", "127.0.0.1.1.1")),
        ads_port=int(raw_session.get("ads_port", 851)),
        ads_symbol=str(raw_session.get("ads_symbol", "MAIN.bReady")),
        output_root=_path(str(raw_session.get("output_root", "runtime")), WORKSPACE_ROOT),
        snapshot_root=_path(str(raw_session.get("snapshot_root", "runtime/snapshots")), WORKSPACE_ROOT),
        archive_root=_path(str(raw_session.get("archive_root", "runtime/archive")), WORKSPACE_ROOT),
        report_root=_path(str(raw_session.get("report_root", "runtime/reports")), WORKSPACE_ROOT),
    )

    return AppConfig(path=config_path, dialog=dialog, resources=resources, security=security, session=session)
