from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from automation.core.config import DialogConfig, DialogRuleConfig, load_config


class DialogCategory(str, Enum):
    LICENSE = "license"
    ACTIVATION = "activation"
    RESTART = "restart"
    DOWNLOAD = "download"
    TARGET = "target"
    SAVE = "save"
    DELETE = "delete"
    BUILD_ERROR = "build_error"
    SAFETY = "safety"
    OS_SECURITY = "os_security"
    INFORMATIONAL = "informational"
    UNKNOWN = "unknown"


class DialogAction(str, Enum):
    APPROVE = "approve"
    CANCEL = "cancel"
    HUMAN = "human"
    IGNORE = "ignore"


@dataclass(frozen=True)
class DialogObservation:
    title: str
    body: str
    buttons: tuple[str, ...]
    owned_by_us: bool = True
    modal: bool = True
    has_password: bool = False
    has_edit: bool = False
    has_checkbox: bool = False
    sandbox: bool = False
    local_target: bool = False
    project_copy: bool = False
    snapshot_available: bool = False
    allow_license_automation: bool = False
    configured_target: str = ""
    selected_target: str = ""


@dataclass(frozen=True)
class DialogDecision:
    category: DialogCategory
    action: DialogAction
    button: str | None
    confidence: float
    reason: str

    @property
    def requires_human(self) -> bool:
        return self.action is DialogAction.HUMAN


SAFETY_WORDS = (
    "twinsafe",
    "safety",
    "emergency stop",
    "e-stop",
    "safe plc",
    "safety plc",
)
OS_SECURITY_WORDS = (
    "user account control",
    "uac",
    "windows security",
    "windows defender",
    "firewall",
    "driver",
    "publisher",
    "trust",
    "reboot",
    "restart computer",
)
LICENSE_WORDS = ("license", "licence", "trial", "security code", "activation key")
ACTIVATION_WORDS = ("activate configuration", "activation", "overwrite", "old configuration")
RESTART_WORDS = ("restart twincat", "run mode", "start twinCAT", "restart the system")
DOWNLOAD_WORDS = ("download", "load program", "plc program", "controller")
TARGET_WORDS = ("target system", "choose target", "netid", "net id", "route")
SAVE_WORDS = ("save changes", "save project", "save configuration", "do you want to save")
DELETE_WORDS = ("delete", "remove", "overwrite", "discard")
BUILD_WORDS = ("build failed", "compile error", "error list", "syntax error", "unknown type")
INFO_WORDS = ("completed", "success", "information", "ready", "finished")


def _text(observation: DialogObservation) -> str:
    return f"{observation.title}\n{observation.body}".casefold()


def _button(observation: DialogObservation, *candidates: str) -> str | None:
    normalized = {button.casefold(): button for button in observation.buttons}
    for candidate in candidates:
        if candidate.casefold() in normalized:
            return normalized[candidate.casefold()]
    return None


class DialogPolicy:
    """Content-aware dialog policy driven by config/automation.toml."""

    def __init__(self, config: DialogConfig | None = None) -> None:
        if config is None:
            try:
                config = load_config().dialog
            except FileNotFoundError:
                config = DialogConfig()
        self.config = config

    def classify(self, observation: DialogObservation) -> DialogDecision:
        if not observation.owned_by_us:
            return DialogDecision(DialogCategory.UNKNOWN, DialogAction.HUMAN, None, 1.0, "dialog is not owned by this XAE")
        if not observation.modal:
            return DialogDecision(DialogCategory.UNKNOWN, DialogAction.IGNORE, None, 0.8, "window is not modal")

        for rule in self.config.deny:
            if self._matches(rule, observation):
                category = self._category(rule)
                return DialogDecision(category, DialogAction.HUMAN, None, 0.99, f"deny rule: {rule.id}")

        for rule in self.config.allow:
            if not self._matches(rule, observation):
                continue
            missing = [name for name in rule.requires if not self._context(observation).get(name, False)]
            if missing:
                return DialogDecision(
                    self._category(rule),
                    DialogAction.HUMAN,
                    None,
                    0.95,
                    f"allow rule {rule.id} requires: {', '.join(missing)}",
                )
            return self._decision(rule, observation)

        return self._fallback(observation)

    def _matches(self, rule: DialogRuleConfig, observation: DialogObservation) -> bool:
        title = observation.title.casefold()
        body = observation.body.casefold()
        if rule.title_any and not any(item.casefold() in title for item in rule.title_any):
            return False
        if rule.body_any and not any(item.casefold() in body for item in rule.body_any):
            return False
        if rule.buttons:
            available = {button.casefold() for button in observation.buttons}
            if not any(button.casefold() in available for button in rule.buttons):
                return False
        return True

    @staticmethod
    def _context(observation: DialogObservation) -> dict[str, bool]:
        return {
            "sandbox": observation.sandbox,
            "local_target": observation.local_target,
            "project_copy": observation.project_copy,
            "snapshot_available": observation.snapshot_available,
            "license_automation": observation.allow_license_automation,
            "no_edit": not observation.has_edit,
            "no_password": not observation.has_password,
            "no_checkbox": not observation.has_checkbox,
        }

    @staticmethod
    def _category(rule: DialogRuleConfig) -> DialogCategory:
        try:
            return DialogCategory(rule.category)
        except ValueError:
            return DialogCategory.UNKNOWN

    def _decision(self, rule: DialogRuleConfig, observation: DialogObservation) -> DialogDecision:
        try:
            action = DialogAction(rule.action)
        except ValueError:
            action = DialogAction.HUMAN
        category = self._category(rule)
        button = self._rule_button(rule, observation, action)
        if action in {DialogAction.APPROVE, DialogAction.CANCEL} and not button:
            return DialogDecision(category, DialogAction.HUMAN, None, 0.9, f"rule {rule.id} has no actionable button")
        return DialogDecision(category, action, button, 0.9, f"allow rule: {rule.id}")

    @staticmethod
    def _rule_button(rule: DialogRuleConfig, observation: DialogObservation, action: DialogAction) -> str | None:
        if rule.buttons:
            for candidate in rule.buttons:
                selected = _button(observation, candidate)
                if selected:
                    return selected
        if action is DialogAction.APPROVE:
            return _button(observation, "Yes", "OK", "Continue", "Confirm", "Retry", "是", "确定", "确认", "重试")
        if action is DialogAction.CANCEL:
            return _button(observation, "No", "Cancel", "Close", "否", "取消", "关闭")
        return None

    def _fallback(self, observation: DialogObservation) -> DialogDecision:
        return _classify_fallback(observation, self.config)


def _classify_fallback(observation: DialogObservation, config: DialogConfig) -> DialogDecision:
    text = _text(observation)

    if not observation.owned_by_us:
        return DialogDecision(DialogCategory.UNKNOWN, DialogAction.HUMAN, None, 1.0, "dialog is not owned by this XAE")
    if not observation.modal:
        return DialogDecision(DialogCategory.UNKNOWN, DialogAction.IGNORE, None, 0.8, "window is not modal")

    if any(word in text for word in SAFETY_WORDS):
        return DialogDecision(DialogCategory.SAFETY, DialogAction.HUMAN, None, 0.99, "safety/TwinSAFE dialog")
    if any(word in text for word in OS_SECURITY_WORDS):
        return DialogDecision(DialogCategory.OS_SECURITY, DialogAction.HUMAN, None, 0.99, "OS/security dialog")
    if any(word in text for word in BUILD_WORDS):
        return DialogDecision(DialogCategory.BUILD_ERROR, DialogAction.HUMAN, None, 0.95, "build/compiler diagnostics must not be dismissed")
    if observation.has_password or observation.has_checkbox:
        return DialogDecision(DialogCategory.UNKNOWN, DialogAction.HUMAN, None, 0.95, "dialog contains a password or persistent checkbox")

    if any(word in text for word in LICENSE_WORDS):
        if observation.has_edit:
            return DialogDecision(DialogCategory.LICENSE, DialogAction.HUMAN, None, 0.95, "license text entry requires a human")
        if observation.allow_license_automation and observation.sandbox:
            button = _button(observation, "Yes", "OK", "Continue", "确认")
            return DialogDecision(DialogCategory.LICENSE, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.85, "license auto-accept explicitly enabled in sandbox")
        return DialogDecision(DialogCategory.LICENSE, DialogAction.HUMAN, None, 0.95, "license automation is disabled")

    if any(word in text for word in TARGET_WORDS):
        if observation.sandbox and observation.configured_target and observation.selected_target:
            if observation.configured_target == observation.selected_target:
                button = _button(observation, "OK", "Yes", "Continue", "确认")
                return DialogDecision(DialogCategory.TARGET, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.9, "selected target matches configured local target")
            return DialogDecision(DialogCategory.TARGET, DialogAction.CANCEL, _button(observation, "Cancel", "否", "取消"), 0.98, "selected target differs from configured target")
        return DialogDecision(DialogCategory.TARGET, DialogAction.HUMAN, None, 0.95, "target change requires explicit configuration")

    if any(word in text for word in ACTIVATION_WORDS):
        if observation.sandbox and observation.local_target and observation.project_copy and observation.snapshot_available:
            button = _button(observation, "Yes", "OK", "Confirm", "确认")
            return DialogDecision(DialogCategory.ACTIVATION, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.9, "local simulation activation with snapshot")
        return DialogDecision(DialogCategory.ACTIVATION, DialogAction.HUMAN, None, 0.98, "activation requires local sandbox, copy, and snapshot")

    if any(word in text for word in RESTART_WORDS):
        if observation.sandbox and observation.local_target:
            button = _button(observation, "Yes", "OK", "Confirm", "确认")
            return DialogDecision(DialogCategory.RESTART, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.9, "local simulation restart")
        return DialogDecision(DialogCategory.RESTART, DialogAction.HUMAN, None, 0.98, "runtime restart requires local simulation")

    if any(word in text for word in DOWNLOAD_WORDS):
        if observation.sandbox and observation.local_target and observation.project_copy:
            button = _button(observation, "Yes", "OK", "Download", "确认")
            return DialogDecision(DialogCategory.DOWNLOAD, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.85, "local simulation download")
        return DialogDecision(DialogCategory.DOWNLOAD, DialogAction.HUMAN, None, 0.98, "program download requires local simulation and a project copy")

    if any(word in text for word in SAVE_WORDS):
        if observation.sandbox and observation.project_copy:
            button = _button(observation, "Save", "Yes", "是", "保存")
            return DialogDecision(DialogCategory.SAVE, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.85, "save into a disposable project copy")
        return DialogDecision(DialogCategory.SAVE, DialogAction.HUMAN, None, 0.95, "save prompt on a non-copy project")

    if any(word in text for word in DELETE_WORDS):
        if observation.sandbox and observation.project_copy:
            button = _button(observation, "Yes", "OK", "Delete", "确认")
            return DialogDecision(DialogCategory.DELETE, DialogAction.APPROVE if button else DialogAction.HUMAN, button, 0.7, "destructive action in a disposable copy")
        return DialogDecision(DialogCategory.DELETE, DialogAction.HUMAN, None, 0.95, "destructive action outside a disposable copy")

    if any(word in text for word in INFO_WORDS) and not observation.has_edit:
        button = _button(observation, "OK", "Close", "确定", "关闭")
        if button:
            return DialogDecision(DialogCategory.INFORMATIONAL, DialogAction.APPROVE, button, 0.85, "informational message")

    cancel = _button(observation, "Cancel", "Close", "No", "取消", "关闭", "否")
    if cancel and config.allow_unknown_close:
        return DialogDecision(DialogCategory.UNKNOWN, DialogAction.CANCEL, cancel, 0.75, "unknown dialog; safe close only")
    if config.allow_unknown_approve and observation.sandbox:
        button = _button(observation, "OK", "Close", "确定", "关闭")
        if button and not observation.has_edit and not observation.has_password:
            return DialogDecision(DialogCategory.UNKNOWN, DialogAction.APPROVE, button, 0.55, "unknown informational dialog explicitly allowed")
    return DialogDecision(DialogCategory.UNKNOWN, DialogAction.HUMAN, None, 0.9, "unknown dialog has no safe close button")


def classify_dialog(
    observation: DialogObservation,
    policy: DialogPolicy | None = None,
) -> DialogDecision:
    return (policy or DialogPolicy()).classify(observation)
