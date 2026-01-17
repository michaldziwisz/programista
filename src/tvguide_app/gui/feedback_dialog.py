from __future__ import annotations

import logging
import os
from pathlib import Path
import threading
from typing import Any, Literal

import wx

from sygnalista_reporter import ReportError, send_report

ReportKind = Literal["bug", "suggestion"]

_DEFAULT_SYGNALISTA_BASE_URL = "https://sygnalista.michaldziwisz.workers.dev"

try:

    class _NamedAccessible(wx.Accessible):
        def __init__(self, window: wx.Window, name: str, description: str | None = None) -> None:
            super().__init__(window)
            self._window = window
            self._name = name
            self._description = description or ""

        def GetName(self, childId: int):  # noqa: N802 - wx API name
            return (wx.ACC_OK, self._name)

        def GetDescription(self, childId: int):  # noqa: N802 - wx API name
            if self._description:
                return (wx.ACC_OK, self._description)
            return (wx.ACC_NOT_SUPPORTED, "")

        def GetRole(self, childId: int):  # noqa: N802 - wx API name
            if isinstance(self._window, wx.TextCtrl):
                return (wx.ACC_OK, wx.ROLE_SYSTEM_TEXT)
            return (wx.ACC_OK, wx.ROLE_SYSTEM_CLIENT)

        def GetState(self, childId: int):  # noqa: N802 - wx API name
            state = 0
            if not self._window.IsEnabled():
                state |= wx.ACC_STATE_SYSTEM_UNAVAILABLE
            if not self._window.IsShownOnScreen():
                state |= wx.ACC_STATE_SYSTEM_INVISIBLE
            if self._window.HasFocus():
                state |= wx.ACC_STATE_SYSTEM_FOCUSED
            if self._window.CanAcceptFocus():
                state |= wx.ACC_STATE_SYSTEM_FOCUSABLE

            if isinstance(self._window, wx.TextCtrl):
                if not self._window.IsEditable():
                    state |= wx.ACC_STATE_SYSTEM_READONLY
                if self._window.GetWindowStyleFlag() & wx.TE_PASSWORD:
                    state |= wx.ACC_STATE_SYSTEM_PROTECTED

            return (wx.ACC_OK, state)

        def GetValue(self, childId: int):  # noqa: N802 - wx API name
            if isinstance(self._window, wx.TextCtrl):
                if self._window.GetWindowStyleFlag() & wx.TE_PASSWORD:
                    return (wx.ACC_OK, "")
                return (wx.ACC_OK, self._window.GetValue())
            return (wx.ACC_NOT_SUPPORTED, "")

except Exception:  # pragma: no cover - accessibility may be unavailable
    _NamedAccessible = None  # type: ignore[assignment]


def _a11y(control: wx.Window, name: str) -> None:
    try:
        control.SetName(name)
    except Exception:
        pass

    try:
        control.SetHelpText(name)
    except Exception:
        pass

    if _NamedAccessible is None:
        return
    if not isinstance(control, wx.TextCtrl):
        return

    try:
        acc = _NamedAccessible(control, name, name)
        control.SetAccessible(acc)
        setattr(control, "_programista_accessible", acc)
    except Exception:
        pass


def _resolve_base_url() -> str:
    for key in ("PROGRAMISTA_SYGNALISTA_URL", "SYGNALISTA_BASE_URL", "SYGNALISTA_URL"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return _DEFAULT_SYGNALISTA_BASE_URL


def _resolve_app_token() -> str:
    for key in ("PROGRAMISTA_SYGNALISTA_APP_TOKEN", "SYGNALISTA_APP_TOKEN"):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    return ""


def _detect_log_path() -> Path | None:
    candidates: list[Path] = []
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                path = Path(handler.baseFilename)
            except Exception:  # noqa: BLE001
                continue
            if path.exists() and path.is_file():
                candidates.append(path)

    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


class FeedbackDialog(wx.Dialog):
    def __init__(self, parent: wx.Window, *, app_version: str) -> None:
        super().__init__(parent, title="Zgłoszenie do autora", style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.SetName("feedback_dialog")

        self._sending = False
        self._base_url = _resolve_base_url()
        self._app_token = _resolve_app_token()
        self._app_version = app_version
        self._default_log_path = _detect_log_path()

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        self._kind = wx.RadioBox(
            panel,
            label="Kategoria",
            choices=["Błąd", "Sugestia"],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        _a11y(self._kind, self._kind.GetLabel())

        title_label = wx.StaticText(panel, label="Tytuł")
        self._title = wx.TextCtrl(panel)
        _a11y(self._title, title_label.GetLabel())

        description_label = wx.StaticText(panel, label="Opis")
        self._description = wx.TextCtrl(panel, style=wx.TE_MULTILINE, size=(-1, 160))
        _a11y(self._description, description_label.GetLabel())

        email_label = wx.StaticText(panel, label="E-mail (opcjonalnie)")
        self._email = wx.TextCtrl(panel)
        _a11y(self._email, email_label.GetLabel())

        warning = wx.StaticText(
            panel,
            label="Uwaga: jeśli wpiszesz e-mail, będzie publiczny w zgłoszeniu na GitHub.",
        )
        warning.Wrap(560)

        self._include_logs = wx.CheckBox(panel, label="Dołącz log")
        self._log_path = wx.TextCtrl(panel)
        browse = wx.Button(panel, label="Wybierz…")
        browse.Bind(wx.EVT_BUTTON, self._on_browse_log)
        _a11y(self._include_logs, self._include_logs.GetLabel())
        _a11y(self._log_path, "Plik logu")
        _a11y(browse, browse.GetLabel())

        if self._default_log_path is not None:
            self._log_path.SetValue(str(self._default_log_path))
            self._include_logs.SetValue(True)
        else:
            self._include_logs.SetValue(False)

        self._status = wx.StaticText(panel, label="")

        form = wx.FlexGridSizer(cols=2, vgap=8, hgap=10)
        form.AddGrowableCol(1, proportion=1)

        form.Add(title_label, 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self._title, 1, wx.EXPAND)

        form.Add(description_label, 0, wx.ALIGN_TOP)
        form.Add(self._description, 1, wx.EXPAND)

        form.Add(email_label, 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(self._email, 1, wx.EXPAND)

        form.Add(wx.StaticText(panel, label=""), 0)
        form.Add(warning, 1, wx.EXPAND)

        log_row = wx.BoxSizer(wx.HORIZONTAL)
        log_row.Add(self._log_path, 1, wx.EXPAND)
        log_row.Add(browse, 0, wx.LEFT, 8)

        form.Add(self._include_logs, 0, wx.ALIGN_CENTER_VERTICAL)
        form.Add(log_row, 1, wx.EXPAND)

        root.Add(self._kind, 0, wx.EXPAND | wx.ALL, 12)
        root.Add(form, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)
        root.Add(self._status, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        btns = wx.StdDialogButtonSizer()
        self._send_btn = wx.Button(panel, wx.ID_OK, "Wyślij")
        self._cancel_btn = wx.Button(panel, wx.ID_CANCEL, "Anuluj")
        _a11y(self._send_btn, self._send_btn.GetLabel())
        _a11y(self._cancel_btn, self._cancel_btn.GetLabel())
        btns.AddButton(self._send_btn)
        btns.AddButton(self._cancel_btn)
        btns.Realize()
        root.Add(btns, 0, wx.EXPAND | wx.ALL, 12)

        panel.SetSizer(root)
        root.Fit(self)
        self.SetMinSize((560, 520))

        self._send_btn.Bind(wx.EVT_BUTTON, self._on_send)

        self.CentreOnParent()

    def _on_browse_log(self, _evt: wx.CommandEvent) -> None:
        default_path = Path(self._log_path.GetValue()) if self._log_path.GetValue().strip() else None
        default_dir = str(default_path.parent) if default_path and default_path.parent.exists() else ""
        wildcard = "Pliki logów (*.log;*.txt;*.gz)|*.log;*.txt;*.gz|Wszystkie pliki|*.*"
        dlg = wx.FileDialog(
            self,
            message="Wybierz plik logu",
            defaultDir=default_dir,
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            self._log_path.SetValue(dlg.GetPath())
            self._include_logs.SetValue(True)
        finally:
            dlg.Destroy()

    def _set_sending(self, sending: bool) -> None:
        self._sending = sending
        for ctrl in (
            self._kind,
            self._title,
            self._description,
            self._email,
            self._include_logs,
            self._log_path,
            self._send_btn,
            self._cancel_btn,
        ):
            ctrl.Enable(not sending)
        self._status.SetLabel("Wysyłanie…" if sending else "")

    def _on_send(self, _evt: wx.CommandEvent) -> None:
        if self._sending:
            return

        base_url = _resolve_base_url()
        if not base_url:
            wx.MessageBox(
                "Sygnalista nie jest skonfigurowany. Ustaw PROGRAMISTA_SYGNALISTA_URL lub SYGNALISTA_BASE_URL.",
                "Błąd",
                parent=self,
                style=wx.ICON_ERROR,
            )
            return

        title = self._title.GetValue().strip()
        if not title:
            wx.MessageBox("Tytuł jest wymagany.", "Błąd", parent=self, style=wx.ICON_ERROR)
            return

        description = self._description.GetValue().strip()
        if not description:
            wx.MessageBox("Opis jest wymagany.", "Błąd", parent=self, style=wx.ICON_ERROR)
            return

        email = self._email.GetValue().strip() or None

        kind: ReportKind = "bug" if self._kind.GetSelection() == 0 else "suggestion"

        log_path = None
        if self._include_logs.GetValue():
            value = self._log_path.GetValue().strip()
            log_path = value or None

        diagnostics_extra: dict[str, Any] = {
            "wx": {"platform": wx.Platform},
        }

        self._set_sending(True)
        thread = threading.Thread(
            target=self._send_worker,
            kwargs={
                "base_url": base_url,
                "kind": kind,
                "title": title,
                "description": description,
                "email": email,
                "log_path": log_path,
                "diagnostics_extra": diagnostics_extra,
            },
            daemon=True,
        )
        thread.start()

    def _send_worker(
        self,
        *,
        base_url: str,
        kind: ReportKind,
        title: str,
        description: str,
        email: str | None,
        log_path: str | None,
        diagnostics_extra: dict[str, Any],
    ) -> None:
        try:
            result = send_report(
                base_url=base_url,
                app_id="programista",
                app_version=self._app_version,
                kind=kind,
                title=title,
                description=description,
                email=email,
                log_path=log_path,
                app_token=self._app_token or None,
                diagnostics_extra=diagnostics_extra,
            )
        except ReportError as err:
            wx.CallAfter(self._on_error, "Nie udało się wysłać", f"HTTP {err.status}\n{err.payload!r}")
            return
        except Exception as err:  # noqa: BLE001
            wx.CallAfter(self._on_error, "Nie udało się wysłać", str(err))
            return

        wx.CallAfter(self._on_success, result)

    def _on_success(self, result: Any) -> None:
        self._set_sending(False)
        issue_url = None
        try:
            issue_url = (result or {}).get("issue", {}).get("html_url")
        except Exception:  # noqa: BLE001
            issue_url = None

        if issue_url:
            wx.MessageBox(f"Utworzono issue na GitHub:\n{issue_url}", "Dzięki", parent=self)
        else:
            wx.MessageBox("Wysłano zgłoszenie.", "Dzięki", parent=self)

        self.EndModal(wx.ID_OK)

    def _on_error(self, title: str, message: str) -> None:
        self._set_sending(False)
        wx.MessageBox(message, title, parent=self, style=wx.ICON_ERROR)
