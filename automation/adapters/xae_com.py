from __future__ import annotations

import re
import shutil
import time
import uuid
from pathlib import Path

from automation.core.models import ActivateResult, BuildResult, Diagnostic, ProjectSpec

DEFAULT_PROG_ID = "TcXaeShell.DTE.17.0"
TEMPLATE_ROOT = Path(__file__).resolve().parent.parent.parent / "templates"
SKIP_NAMES = {"_Boot", "_CompileInfo", ".git", ".vs", "bin", "obj"}
SKIP_SUFFIXES = (".tmc", ".bak", ".tclrs")
COM_REJECTED = {
    -2147418111,
    -2147417846,
    -2147417845,
    -2147418110,
    -2147024891,
    -2147352573,  # DISP_E_MEMBERNOTFOUND: the automation tree is not ready yet
}


def _is_error_diagnostic(item: Diagnostic) -> bool:
    level = item.level.casefold()
    return "error" in level or "high" in level


BUILD_ERROR_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+)\)\s*:\s*(?P<level>error|warning)\s*:\s*"
    r"(?P<code>[A-Za-z0-9_]+)\s*:\s*(?P<message>.+)$",
    re.IGNORECASE,
)


def _parse_build_output(text: str) -> list[Diagnostic]:
    """Extract PLC compiler errors from the XAE Build output pane.

    The ErrorList often only carries the generic MSBuild message; the Build
    pane keeps ``file(line) : error: C0046: ...`` lines with the real cause.
    """
    diagnostics: list[Diagnostic] = []
    for raw in text.splitlines():
        match = BUILD_ERROR_RE.match(raw.strip())
        if not match:
            continue
        diagnostics.append(
            Diagnostic(
                level=match.group("level").casefold(),
                message=f"{match.group('code')}: {match.group('message').strip()}",
                file=match.group("file").strip(),
                line=int(match.group("line")),
                column=0,
                project="",
            )
        )
    return diagnostics


def _new_pou_id() -> str:
    return "{" + str(uuid.uuid4()) + "}"


def _id_attr(value: str) -> str:
    value = value.strip()
    if value.startswith("{") and value.endswith("}"):
        return value
    return "{" + value + "}"


def build_pou_xml(
    name: str,
    declaration: str,
    implementation: str = "",
    methods: list[dict] | None = None,
    pou_id: str | None = None,
) -> str:
    """Build a complete .TcPOU document.

    TwinCAT's ``DeclarationText`` setter only accepts the POU header.  Method
    bodies must be separate ``<Method>`` elements; stuffing ``METHOD ...``
    blocks into the declaration makes the FB compile in isolation but hides
    the methods from the type system, so other POUs report "Unknown type".
    """

    def cdata(text: str) -> str:
        return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<TcPlcObject Version="1.1.0.1">',
        f'  <POU Name="{name}" Id="{_id_attr(pou_id or _new_pou_id())}" SpecialFunc="None">',
        f"    <Declaration>{cdata(declaration)}</Declaration>",
        f"    <Implementation><ST>{cdata(implementation)}</ST></Implementation>",
    ]
    for method in methods or []:
        method_name = str(method["name"])
        method_declaration = str(method.get("declaration", ""))
        method_implementation = str(method.get("implementation", ""))
        method_id = _id_attr(str(method.get("id") or _new_pou_id()))
        lines.extend(
            [
                f'    <Method Name="{method_name}" Id="{method_id}">',
                f"      <Declaration>{cdata(method_declaration)}</Declaration>",
                f"      <Implementation><ST>{cdata(method_implementation)}</ST></Implementation>",
                "    </Method>",
            ]
        )
    lines.extend(["  </POU>", "</TcPlcObject>"])
    return "\n".join(lines) + "\n"


class XaeDteAdapter:
    """TwinCAT XAE adapter using the documented DTE Automation Interface.

    All COM work stays in this adapter. The adapter is single-instance and
    refuses to attach to an already-running XAE shell so that the runner can
    guarantee one XAE at a time.
    """

    def __init__(self, prog_id: str = DEFAULT_PROG_ID) -> None:
        self.prog_id = prog_id
        self._dte = None
        self._project = None
        self._sysman = None
        self._com_initialized = False
        self._solution: Path | None = None

    def prepare_project(self, spec: ProjectSpec) -> Path:
        source = spec.source_solution.expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"source solution not found: {source}")
        try:
            source.relative_to(TEMPLATE_ROOT.resolve())
        except ValueError as exc:
            raise ValueError(f"source solution is outside the template root: {source}") from exc
        workspace = spec.workspace.expanduser().resolve()
        target_root = (workspace / spec.name).resolve()
        try:
            target_root.relative_to(workspace)
        except ValueError as exc:
            raise ValueError(f"project target escapes workspace: {target_root}") from exc
        if target_root.is_relative_to(source.parent) or source.parent.is_relative_to(target_root):
            raise ValueError("source and target overlap")
        if target_root.exists():
            raise FileExistsError(f"project workspace already exists: {target_root}")
        target_root.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source.parent, target_root, ignore=self._ignore)
        self._solution = target_root / source.name
        if not self._solution.is_file():
            raise FileNotFoundError(f"copied solution not found: {self._solution}")
        return self._solution

    def open_solution(self, solution: Path) -> None:
        self._ensure_com()
        if self._dte is None:
            if self._has_running_xae():
                # A previous session may still be shutting down; give it a
                # moment before declaring the workspace busy.
                try:
                    self.wait_for_exit(5.0)
                except Exception as exc:
                    raise RuntimeError(
                        "a TcXaeShell instance is already running; close it before automation"
                    ) from exc
            import win32com.client

            self._dte = win32com.client.Dispatch(self.prog_id)
            self._dte.SuppressUI = True
            self._dte.MainWindow.Visible = False
            self._dte.UserControl = False
            # SuppressUI only covers Visual Studio dialogs. TwinCAT's own
            # prompts (e.g. "unrestored variable links") need SilentMode.
            try:
                settings = self._dte.GetObject("TcAutomationSettings")
                settings.SilentMode = True
            except Exception:
                pass
        self._solution = solution
        self._retry(lambda: self._dte.Solution.Open(str(solution)))
        self._project = self._retry(lambda: self._dte.Solution.Projects.Item(1))
        project_path = Path(self._project.FullName).expanduser().resolve()
        try:
            project_path.relative_to(Path(solution).expanduser().resolve().parent)
        except ValueError as exc:
            raise RuntimeError(f"opened project is outside the copied workspace: {project_path}") from exc
        self._sysman = self._retry(lambda: self._project.Object)
        if self._sysman is None:
            raise RuntimeError("solution has no TwinCAT system manager project")

    def write_plc(self, solution: Path, spec: ProjectSpec) -> Path:
        return self.write_pou(
            solution,
            spec,
            "MAIN",
            spec.plc_declaration,
            spec.plc_implementation,
            "program",
        )

    def write_pou(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
        implementation: str,
        kind: str = "function_block",
        methods: list[dict] | None = None,
    ) -> Path:
        self._require_open()
        kind_map = {"program": 602, "function": 603, "function_block": 604}
        subtype = kind_map.get(kind.casefold())
        if subtype is None:
            raise ValueError(f"unsupported POU kind: {kind}")

        def write_once() -> None:
            pous = self._find_child(self._nested_project(spec), "POUs")
            try:
                item = self._find_child(pous, name)
            except LookupError:
                item = self._retry(lambda: pous.CreateChild(name, subtype, "", 1))
            if methods:
                # The DeclarationText setter cannot create method objects.
                # Write the full POU document so methods become real children
                # of the POU and are visible to the compiler.
                existing = str(item.ProduceXml())
                match = re.search(r'<POU\b[^>]*\bId="([^"]+)"', existing)
                pou_id = match.group(1) if match else None
                item.DocumentXml = build_pou_xml(
                    name,
                    declaration,
                    implementation,
                    methods,
                    pou_id=pou_id,
                )
            else:
                item.DeclarationText = declaration
                item.ImplementationText = implementation
            self._project.Save()

        self._retry(write_once)
        return solution

    def build(self, solution: Path, spec: ProjectSpec) -> BuildResult:
        self._require_open()
        if self._sysman is None:
            raise RuntimeError("solution is not open")

        def build_once() -> None:
            try:
                self._dte.ExecuteCommand("OtherContextMenus.ErrorList.Clear")
            except Exception:
                pass
            try:
                # A stale incremental build can produce an app without a usable
                # runtime symbol table.  Clean first so activation and ADS
                # symbol upload are deterministic.
                self._dte.Solution.SolutionBuild.Clean(True)
            except Exception:
                pass
            self._sysman.ConfigurationManager.ActiveTargetPlatform = spec.target_platform
            self._dte.Solution.SolutionBuild.BuildProject(
                f"{spec.build_configuration}|{spec.target_platform}",
                self._project.FullName,
                True,
            )

        self._retry(build_once)
        last = int(self._dte.Solution.SolutionBuild.LastBuildInfo)
        diagnostics = list(self._read_diagnostics())
        if last != 0:
            if not any(_is_error_diagnostic(item) for item in diagnostics):
                # PLC compiler errors can show up in the ErrorList a moment
                # after LastBuildInfo flips.
                for _ in range(4):
                    time.sleep(0.4)
                    retry = self._read_diagnostics()
                    if retry:
                        diagnostics = list(retry)
                    if any(_is_error_diagnostic(item) for item in diagnostics):
                        break
            seen = {
                (item.file.casefold(), item.line, item.message.casefold())
                for item in diagnostics
            }
            for extra in self._read_build_output():
                key = (extra.file.casefold(), extra.line, extra.message.casefold())
                if key not in seen:
                    seen.add(key)
                    diagnostics.append(extra)
        diagnostics = tuple(diagnostics)
        error_count = sum(
            1 for item in diagnostics if "high" in item.level.casefold() or "error" in item.level.casefold()
        )
        warning_count = sum(
            1 for item in diagnostics if "warning" in item.level.casefold() or "medium" in item.level.casefold()
        )
        return BuildResult(
            ok=last == 0,
            errors=error_count or last,
            warnings=warning_count,
            message="build succeeded" if last == 0 else f"build failed ({last})",
            diagnostics=diagnostics,
        )

    def verify_symbol(self, solution: Path, spec: ProjectSpec, symbol: str | None = None) -> None:
        self._require_open()
        tmc = self._tmc_path(spec)
        import xml.etree.ElementTree as ET

        root = ET.parse(tmc).getroot()
        target = symbol or spec.ads_symbol
        names = {element.findtext("Name") for element in root.iter("Symbol")}
        if target not in names:
            raise RuntimeError(
                f"symbol {target!r} not found in {tmc.name} "
                f"({len([name for name in names if name])} symbols)"
            )

    def list_pous(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        self._require_open()

        def read_pous() -> list[dict]:
            pous = self._pou_folder(spec)
            result = []
            for index in range(1, int(pous.ChildCount) + 1):
                child = pous.Child(index)
                result.append({"name": str(child.Name), "subtype": int(child.ItemSubType)})
            return result

        return self._retry(read_pous)

    def read_pou(self, solution: Path, spec: ProjectSpec, name: str) -> dict:
        self._require_open()

        def read_one() -> dict:
            item = self._find_child(self._pou_folder(spec), name)
            return {
                "name": str(item.Name),
                "declaration": str(item.DeclarationText),
                "implementation": str(item.ImplementationText),
            }

        return self._retry(read_one)

    def write_dut(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
        kind: str = "struct",
    ) -> Path:
        self._require_open()
        subtype = {"enum": 605, "struct": 606, "union": 607}.get(kind.casefold())
        if subtype is None:
            raise ValueError(f"unsupported DUT kind: {kind}")

        def write_once() -> None:
            folder = self._find_child(self._nested_project(spec), "DUTs")
            try:
                item = self._find_child(folder, name)
            except LookupError:
                item = self._retry(lambda: folder.CreateChild(name, subtype, "", declaration))
            try:
                item.DeclarationText = declaration
            except Exception:
                pass
            self._project.Save()

        self._retry(write_once)
        return solution

    def write_gvl(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        declaration: str,
    ) -> Path:
        self._require_open()

        def write_once() -> None:
            folder = self._find_child(self._nested_project(spec), "GVLs")
            try:
                item = self._find_child(folder, name)
            except LookupError:
                item = self._retry(lambda: folder.CreateChild(name, 615, "", declaration))
            try:
                item.DeclarationText = declaration
            except Exception:
                pass
            self._project.Save()

        self._retry(write_once)
        return solution

    def project_tree(self, solution: Path, spec: ProjectSpec, max_depth: int = 5) -> dict:
        self._require_open()

        def read_tree() -> dict:
            return self._tree_node(self._nested_project(spec), 0, max_depth)

        return self._retry(read_tree)

    def list_symbols(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        self._require_open()
        import xml.etree.ElementTree as ET

        root = ET.parse(self._tmc_path(spec)).getroot()
        result = []
        for element in root.iter("Symbol"):
            name = element.findtext("Name")
            if name:
                result.append(
                    {
                        "name": name,
                        "type": element.findtext("BaseType") or "",
                        "bits": element.findtext("BitSize") or "",
                    }
                )
        return result

    def add_library(
        self,
        solution: Path,
        spec: ProjectSpec,
        name: str,
        version: str = "*",
        company: str = "Beckhoff Automation GmbH",
    ) -> None:
        self._require_open()

        def add_once() -> None:
            refs = self._find_child(self._nested_project(spec), "References")
            refs.AddLibrary(name, version, company)
            self._project.Save()

        self._retry(add_once)

    def list_libraries(self, solution: Path, spec: ProjectSpec) -> list[dict]:
        self._require_open()

        def read_refs() -> list[dict]:
            refs = self._find_child(self._nested_project(spec), "References")
            result = []
            for index in range(1, int(refs.ChildCount) + 1):
                child = refs.Child(index)
                result.append({"name": str(child.Name), "path": str(child.PathName)})
            return result

        return self._retry(read_refs)

    def export_archive(self, solution: Path, spec: ProjectSpec, path: Path, scope: str = "solution") -> Path:
        self._require_open()
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(path)
        if scope == "solution":
            persist = self._retry(lambda: self._dte.GetObject("TcArchivePersist"))
            self._retry(lambda: persist.SaveAsArchive(str(path)))
        elif scope == "plc":
            plc_root = self._sysman.LookupTreeItem("TIPC")
            self._retry(lambda: plc_root.ExportChild(spec.plc_project, str(path)))
        else:
            raise ValueError(f"unsupported archive scope: {scope}")
        if not path.is_file():
            raise RuntimeError(f"archive was not created: {path}")
        return path

    def activate_simulation(self, solution: Path, spec: ProjectSpec) -> ActivateResult:
        self._require_open()
        if self._sysman is None:
            raise RuntimeError("solution is not open")
        target = self._assert_local_target()
        self._retry(self._sysman.ActivateConfiguration, attempts=40, delay=0.75)
        try:
            self._retry(self._sysman.StartRestartTwinCAT, attempts=40, delay=0.75)
        except Exception as exc:
            raise RuntimeError(
                "TwinCAT system restart failed. Start the TcSysSrv service as "
                "administrator or reboot Windows, then rerun acceptance."
            ) from exc
        self._retry(self._project.Save)
        return ActivateResult(
            ok=True,
            target_netid=target,
            platform=spec.target_platform,
            diagnostics=self._read_diagnostics(),
        )

    def close(self) -> None:
        if self._dte is not None:
            dte = self._dte
            self._dte = None
            self._project = None
            self._sysman = None
            for _ in range(3):
                try:
                    dte.Quit()
                except Exception:
                    pass
                try:
                    self.wait_for_exit(10.0)
                    break
                except Exception:
                    continue
            if self._xae_process_running():
                self._terminate_shell()
        if self._com_initialized:
            try:
                import pythoncom

                pythoncom.CoUninitialize()
            finally:
                self._com_initialized = False

    @staticmethod
    def _retry(callable_, attempts: int = 30, delay: float = 0.5):
        import pywintypes

        last_error = None
        for attempt in range(attempts):
            try:
                return callable_()
            except pywintypes.com_error as exc:
                code = getattr(exc, "hresult", exc.args[0] if exc.args else None)
                if code not in COM_REJECTED:
                    raise
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay)
        raise last_error

    def _ensure_com(self) -> None:
        if self._com_initialized:
            return
        import pythoncom

        pythoncom.CoInitialize()
        self._com_initialized = True

    def _has_running_xae(self) -> bool:
        import win32com.client

        try:
            win32com.client.GetActiveObject(self.prog_id)
            return True
        except Exception:
            pass
        return self._xae_process_running()

    @staticmethod
    def _xae_process_running() -> bool:
        import subprocess

        try:
            output = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq TcXaeShell.exe", "/NH"],
                capture_output=True,
                text=True,
                encoding="mbcs",
                errors="replace",
                timeout=10,
                check=False,
            ).stdout
            return "TcXaeShell.exe" in output
        except Exception as exc:
            raise RuntimeError("cannot determine whether TcXaeShell is running") from exc

    @classmethod
    def wait_for_exit(cls, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not cls._xae_process_running():
                return
            time.sleep(0.5)
        if cls._xae_process_running():
            raise RuntimeError("TcXaeShell did not exit within the timeout")

    @staticmethod
    def _terminate_shell() -> None:
        """Force-close a TcXaeShell that ignored Quit.

        The adapter refuses to attach to a pre-existing instance, so any shell
        still alive at this point is the one this adapter started.
        """
        import subprocess

        try:
            subprocess.run(
                ["taskkill", "/IM", "TcXaeShell.exe", "/F", "/T"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except Exception:
            pass

    def _nested_project(self, spec: ProjectSpec):
        last_error = None
        for _ in range(30):
            try:
                plc = self._sysman.LookupTreeItem(f"TIPC^{spec.plc_project}")
                return plc.NestedProject
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(f"PLC project {spec.plc_project!r} did not become ready") from last_error

    def _pou_folder(self, spec: ProjectSpec):
        return self._find_child(self._nested_project(spec), "POUs")

    def _tree_node(self, item, depth: int, max_depth: int) -> dict:
        node = self._retry(
            lambda: {
                "name": str(item.Name),
                "path": str(item.PathName),
                "type": int(item.ItemType),
                "subtype": int(item.ItemSubType),
            }
        )
        if depth >= max_depth:
            node["children_truncated"] = True
            return node
        children = []
        count = min(int(self._retry(lambda: item.ChildCount)), 500)
        for index in range(1, count + 1):
            try:
                child = self._retry(lambda index=index: item.Child(index))
                children.append(self._tree_node(child, depth + 1, max_depth))
            except Exception as exc:
                children.append({"name": f"<error:{index}>", "path": "", "error": str(exc)})
        node["children"] = children
        if int(item.ChildCount) > count:
            node["children_truncated"] = True
        return node

    def _read_build_output(self) -> list[Diagnostic]:
        """Read the XAE Build output pane and parse PLC compiler errors."""
        import json
        import subprocess

        script = Path(__file__).resolve().parent.parent.parent / "scripts" / "read_build_output.ps1"
        if not script.is_file():
            return []
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                capture_output=True,
                text=True,
                encoding="utf-8-sig",
                errors="replace",
                timeout=30,
                check=False,
            )
        except Exception:
            return []
        payload = proc.stdout.strip()
        if not payload:
            return []
        try:
            data = json.loads(payload)
        except Exception:
            return []
        rows = data if isinstance(data, list) else [data]
        diagnostics: list[Diagnostic] = []
        for row in rows:
            if isinstance(row, dict):
                diagnostics.extend(_parse_build_output(str(row.get("text", ""))))
        return diagnostics

    def _read_diagnostics(self) -> tuple[Diagnostic, ...]:
        import json
        import subprocess

        script = Path(__file__).resolve().parent.parent.parent / "scripts" / "read_diagnostics.ps1"
        if script.is_file():
            try:
                proc = subprocess.run(
                    ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    capture_output=True,
                    text=True,
                    encoding="utf-8-sig",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                payload = proc.stdout.strip()
                if payload:
                    data = json.loads(payload)
                    rows = data if isinstance(data, list) else [data]
                    return tuple(
                        Diagnostic(
                            level=str(row.get("level", "")),
                            message=str(row.get("message", "")),
                            file=str(row.get("file", "")),
                            line=int(row.get("line", 0) or 0),
                            column=int(row.get("column", 0) or 0),
                            project=str(row.get("project", "")),
                        )
                        for row in rows
                        if isinstance(row, dict)
                    )
            except Exception:
                pass
        try:
            text = self._sysman.GetLastErrorMessages()
        except Exception:
            return ()
        if not text:
            return ()
        return tuple(
            Diagnostic(level="info", message=line.strip())
            for line in str(text).splitlines()
            if line.strip()
        )

    def _tmc_path(self, spec: ProjectSpec) -> Path:
        project_dir = Path(self._project.FullName).parent
        tmc = project_dir / spec.plc_project / f"{spec.plc_project}.tmc"
        if tmc.is_file():
            return tmc
        candidates = list((project_dir / spec.plc_project).glob("*.tmc"))
        if not candidates:
            raise FileNotFoundError(f"TMC not found for {spec.plc_project}")
        return candidates[0]

    def _assert_local_target(self) -> str:
        import socket

        target = str(self._retry(self._sysman.GetTargetNetId))
        if target.strip() in {"", "0.0.0.0.0.0", "0.0.0.0.0.0.0"}:
            local = next(
                (ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")),
                "127.0.0.1",
            )
            candidate = f"{local}.1.1"
            self._retry(lambda: self._sysman.SetTargetNetId(candidate))
            target = str(self._retry(self._sysman.GetTargetNetId))
        local_ips = {"127.0.0.1"}
        try:
            local_ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
        except OSError:
            pass
        if not any(target.startswith(f"{ip}.") for ip in local_ips):
            raise RuntimeError(
                f"refusing to activate non-local target {target!r}; local IPs: {sorted(local_ips)}"
            )
        return target

    def _require_open(self) -> None:
        if self._dte is None or self._project is None or self._sysman is None:
            raise RuntimeError("solution is not open")

    @staticmethod
    def _find_child(item, name: str):
        """Return a named child, tolerating transient dead COM wrappers.

        A freshly opened solution can hand out one or two child objects whose
        dynamic wrapper is not usable yet (``AttributeError: Child.Name``).
        Re-enumerating the folder after a short delay yields fresh wrappers.
        """
        import pywintypes

        last_error: Exception | None = None
        for _ in range(20):
            try:
                for index in range(1, int(item.ChildCount) + 1):
                    child = XaeDteAdapter._retry(lambda index=index: item.Child(index))
                    child_name = XaeDteAdapter._retry(
                        lambda child=child: str(child.Name)
                    )
                    if child_name.casefold() == name.casefold():
                        return child
                try:
                    where = item.PathName
                except Exception:
                    where = "<unknown>"
                raise LookupError(f"child {name!r} not found under {where}")
            except (AttributeError, pywintypes.com_error) as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(
            f"could not enumerate children looking for {name!r}: {last_error}"
        ) from last_error

    @staticmethod
    def _ignore(directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in SKIP_NAMES or name.endswith(SKIP_SUFFIXES) or name.startswith("~")
        }
