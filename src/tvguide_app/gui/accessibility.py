from __future__ import annotations

import wx
import wx.dataview as dv


class DelegatingAccessible(wx.Accessible):
    def __init__(
        self,
        win: wx.Window,
        delegate: wx.Accessible,
        *,
        name: str | None = None,
        suppress_value: bool = False,
    ) -> None:
        super().__init__(win)
        self._delegate = delegate
        self._name = name
        self._suppress_value = suppress_value

    def GetName(self, childId: int):  # noqa: N802 (wxPython naming)
        if childId == 0 and self._name:
            return wx.ACC_OK, self._name
        return self._delegate.GetName(childId)

    def GetValue(self, childId: int):  # noqa: N802 (wxPython naming)
        if childId == 0 and self._suppress_value:
            return wx.ACC_OK, ""
        return self._delegate.GetValue(childId)

    def DoDefaultAction(self, *args, **kwargs):  # noqa: N802
        return self._delegate.DoDefaultAction(*args, **kwargs)

    def GetChild(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetChild(*args, **kwargs)

    def GetChildCount(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetChildCount(*args, **kwargs)

    def GetDefaultAction(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetDefaultAction(*args, **kwargs)

    def GetDescription(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetDescription(*args, **kwargs)

    def GetFocus(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetFocus(*args, **kwargs)

    def GetHelpText(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetHelpText(*args, **kwargs)

    def GetKeyboardShortcut(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetKeyboardShortcut(*args, **kwargs)

    def GetLocation(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetLocation(*args, **kwargs)

    def GetParent(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetParent(*args, **kwargs)

    def GetRole(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetRole(*args, **kwargs)

    def GetSelections(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetSelections(*args, **kwargs)

    def GetState(self, *args, **kwargs):  # noqa: N802
        return self._delegate.GetState(*args, **kwargs)

    def HitTest(self, *args, **kwargs):  # noqa: N802
        return self._delegate.HitTest(*args, **kwargs)

    def Navigate(self, *args, **kwargs):  # noqa: N802
        return self._delegate.Navigate(*args, **kwargs)

    def Select(self, *args, **kwargs):  # noqa: N802
        return self._delegate.Select(*args, **kwargs)


def install_quiet_accessible(win: wx.Window, *, name: str) -> None:
    win.SetName(name)
    existing = win.GetAccessible()
    if existing is None or isinstance(existing, DelegatingAccessible):
        return
    win.SetAccessible(DelegatingAccessible(win, existing, name=name, suppress_value=True))


class DataViewTreeCtrlAccessible(wx.Accessible):
    def __init__(self, win: wx.Window, tree: dv.DataViewTreeCtrl, *, name: str) -> None:
        super().__init__(win)
        self._tree = tree
        self._name = name
        self._item_cache: dict[int, DataViewTreeItemAccessible] = {}

    def clear_cache(self) -> None:
        self._item_cache.clear()

    def _item_accessible(self, item: dv.DataViewItem) -> "DataViewTreeItemAccessible":
        key = int(item.GetID())
        cached = self._item_cache.get(key)
        if cached is not None and cached.item.IsOk() and cached.item.GetID() == item.GetID():
            return cached
        acc = DataViewTreeItemAccessible(self.GetWindow(), self, item)
        self._item_cache[key] = acc
        return acc

    def GetName(self, childId: int):  # noqa: N802 (wxPython naming)
        if childId == 0:
            return wx.ACC_OK, self._name
        item = self._tree.GetNthChild(dv.NullDataViewItem, childId - 1)
        if not item.IsOk():
            return wx.ACC_INVALID_ARG, ""
        return wx.ACC_OK, self._tree.GetItemText(item)

    def GetRole(self, childId: int):  # noqa: N802
        if childId == 0:
            return wx.ACC_OK, wx.ROLE_SYSTEM_OUTLINE
        return wx.ACC_OK, wx.ROLE_SYSTEM_OUTLINEITEM

    def GetValue(self, _childId: int):  # noqa: N802
        return wx.ACC_OK, ""

    def GetState(self, _childId: int):  # noqa: N802
        state = wx.ACC_STATE_SYSTEM_FOCUSABLE
        win = self.GetWindow()
        if win and win.HasFocus():
            state |= wx.ACC_STATE_SYSTEM_FOCUSED
        return wx.ACC_OK, state

    def GetChildCount(self):  # noqa: N802
        return wx.ACC_OK, self._tree.GetChildCount(dv.NullDataViewItem)

    def GetChild(self, childId: int):  # noqa: N802
        if childId < 1:
            return wx.ACC_INVALID_ARG, None
        item = self._tree.GetNthChild(dv.NullDataViewItem, childId - 1)
        if not item.IsOk():
            return wx.ACC_INVALID_ARG, None
        return wx.ACC_OK, self._item_accessible(item)

    def GetFocus(self, *args, **kwargs):  # noqa: N802
        item = self._tree.GetCurrentItem()
        if not item.IsOk():
            item = self._tree.GetSelection()
        if item.IsOk():
            return wx.ACC_OK, self._item_accessible(item)
        return wx.ACC_FAIL, None


class DataViewTreeItemAccessible(wx.Accessible):
    def __init__(self, win: wx.Window, root: DataViewTreeCtrlAccessible, item: dv.DataViewItem) -> None:
        super().__init__(win)
        self._root = root
        self._tree = root._tree
        self.item = item

    def GetName(self, childId: int):  # noqa: N802
        if childId == 0:
            if not self.item.IsOk():
                return wx.ACC_FAIL, ""
            return wx.ACC_OK, self._tree.GetItemText(self.item)
        child = self._tree.GetNthChild(self.item, childId - 1)
        if not child.IsOk():
            return wx.ACC_INVALID_ARG, ""
        return wx.ACC_OK, self._tree.GetItemText(child)

    def GetRole(self, childId: int):  # noqa: N802
        if childId == 0:
            return wx.ACC_OK, wx.ROLE_SYSTEM_OUTLINEITEM
        return wx.ACC_OK, wx.ROLE_SYSTEM_OUTLINEITEM

    def GetValue(self, _childId: int):  # noqa: N802
        return wx.ACC_OK, ""

    def GetState(self, _childId: int):  # noqa: N802
        if not self.item.IsOk():
            return wx.ACC_FAIL, 0

        state = wx.ACC_STATE_SYSTEM_FOCUSABLE | wx.ACC_STATE_SYSTEM_SELECTABLE
        if self._tree.IsSelected(self.item):
            state |= wx.ACC_STATE_SYSTEM_SELECTED

        win = self.GetWindow()
        if win and win.HasFocus() and self._tree.GetCurrentItem() == self.item:
            state |= wx.ACC_STATE_SYSTEM_FOCUSED

        if self._tree.IsContainer(self.item):
            if self._tree.IsExpanded(self.item):
                state |= wx.ACC_STATE_SYSTEM_EXPANDED
            else:
                state |= wx.ACC_STATE_SYSTEM_COLLAPSED

        return wx.ACC_OK, state

    def GetChildCount(self):  # noqa: N802
        if not self.item.IsOk():
            return wx.ACC_FAIL, 0
        return wx.ACC_OK, self._tree.GetChildCount(self.item)

    def GetChild(self, childId: int):  # noqa: N802
        if not self.item.IsOk() or childId < 1:
            return wx.ACC_INVALID_ARG, None
        child = self._tree.GetNthChild(self.item, childId - 1)
        if not child.IsOk():
            return wx.ACC_INVALID_ARG, None
        return wx.ACC_OK, self._root._item_accessible(child)

    def GetParent(self):  # noqa: N802
        if not self.item.IsOk():
            return wx.ACC_FAIL, None
        parent = self._tree.GetItemParent(self.item)
        if not parent.IsOk():
            return wx.ACC_OK, self._root
        return wx.ACC_OK, self._root._item_accessible(parent)

    def Select(self, childId: int, selectFlags: int):  # noqa: N802
        if childId != 0 or not self.item.IsOk():
            return wx.ACC_INVALID_ARG

        if selectFlags in (wx.ACC_SEL_TAKESELECTION, wx.ACC_SEL_TAKEFOCUS):
            self._tree.Select(self.item)
            self._tree.SetCurrentItem(self.item)
            return wx.ACC_OK

        if selectFlags == wx.ACC_SEL_REMOVESELECTION:
            self._tree.Unselect(self.item)
            return wx.ACC_OK

        return wx.ACC_NOT_SUPPORTED


def install_dataview_tree_accessible(tree: dv.DataViewTreeCtrl, *, name: str) -> None:
    if wx.Platform != "__WXMSW__":
        return
    win = tree.GetMainWindow()
    if not win:
        return
    win.SetName(name)
    existing = win.GetAccessible()
    if isinstance(existing, DataViewTreeCtrlAccessible):
        return
    win.SetAccessible(DataViewTreeCtrlAccessible(win, tree, name=name))


class NotebookTabAccessible(wx.Accessible):
    def __init__(self, win: wx.Window, root: "NotebookAccessible", idx: int) -> None:
        super().__init__(win)
        self._root = root
        self._idx = idx

    def _label(self) -> str:
        nb = self._root._notebook
        if not nb or self._idx < 0 or self._idx >= nb.GetPageCount():
            return ""
        label = nb.GetPageText(self._idx) or ""
        count = nb.GetPageCount()
        if label and count > 0:
            return f"{label} ({self._idx + 1} z {count})"
        return label

    def GetName(self, childId: int):  # noqa: N802
        if childId != 0:
            return wx.ACC_INVALID_ARG, ""
        return wx.ACC_OK, self._label()

    def GetRole(self, childId: int):  # noqa: N802
        if childId != 0:
            return wx.ACC_INVALID_ARG, wx.ROLE_NONE
        return wx.ACC_OK, wx.ROLE_SYSTEM_PAGETAB

    def GetValue(self, _childId: int):  # noqa: N802
        return wx.ACC_OK, ""

    def GetState(self, _childId: int):  # noqa: N802
        nb = self._root._notebook
        if not nb or self._idx < 0 or self._idx >= nb.GetPageCount():
            return wx.ACC_FAIL, 0

        state = wx.ACC_STATE_SYSTEM_FOCUSABLE | wx.ACC_STATE_SYSTEM_SELECTABLE
        if nb.GetSelection() == self._idx:
            state |= wx.ACC_STATE_SYSTEM_SELECTED
            if nb.HasFocus():
                state |= wx.ACC_STATE_SYSTEM_FOCUSED
        return wx.ACC_OK, state

    def GetParent(self):  # noqa: N802
        return wx.ACC_OK, self._root

    def GetChildCount(self):  # noqa: N802
        return wx.ACC_OK, 0

    def DoDefaultAction(self, childId: int):  # noqa: N802
        if childId != 0:
            return wx.ACC_INVALID_ARG
        nb = self._root._notebook
        if not nb:
            return wx.ACC_FAIL
        nb.ChangeSelection(self._idx)
        nb.SetFocus()
        return wx.ACC_OK

    def Select(self, childId: int, selectFlags: int):  # noqa: N802
        if childId != 0:
            return wx.ACC_INVALID_ARG
        if selectFlags not in (wx.ACC_SEL_TAKESELECTION, wx.ACC_SEL_TAKEFOCUS):
            return wx.ACC_NOT_SUPPORTED
        nb = self._root._notebook
        if not nb:
            return wx.ACC_FAIL
        nb.ChangeSelection(self._idx)
        if selectFlags == wx.ACC_SEL_TAKEFOCUS:
            nb.SetFocus()
        return wx.ACC_OK


class NotebookAccessible(wx.Accessible):
    """
    MSW-only: fix NVDA reporting incorrect number of tabs for wx.Notebook.

    Some combinations of wxWidgets/Windows accessibility may expose extra
    children (pages, scroll buttons) as "tabs". Expose only the actual
    page tabs to ATs.
    """

    def __init__(self, win: wx.Window, notebook: wx.Notebook, *, name: str) -> None:
        super().__init__(win)
        self._notebook = notebook
        self._name = name
        self._tab_cache: dict[int, NotebookTabAccessible] = {}

    def _tab(self, idx: int) -> NotebookTabAccessible:
        cached = self._tab_cache.get(idx)
        if cached is not None:
            return cached
        acc = NotebookTabAccessible(self.GetWindow(), self, idx)
        self._tab_cache[idx] = acc
        return acc

    def GetName(self, childId: int):  # noqa: N802
        if childId == 0:
            count = self._notebook.GetPageCount() if self._notebook else 0
            if count > 0:
                return wx.ACC_OK, f"{self._name} ({count})"
            return wx.ACC_OK, self._name
        idx = childId - 1
        if not self._notebook or idx < 0 or idx >= self._notebook.GetPageCount():
            return wx.ACC_INVALID_ARG, ""
        return wx.ACC_OK, self._notebook.GetPageText(idx) or ""

    def GetRole(self, childId: int):  # noqa: N802
        if childId == 0:
            return wx.ACC_OK, wx.ROLE_SYSTEM_PAGETABLIST
        return wx.ACC_OK, wx.ROLE_SYSTEM_PAGETAB

    def GetValue(self, _childId: int):  # noqa: N802
        return wx.ACC_OK, ""

    def GetState(self, _childId: int):  # noqa: N802
        state = wx.ACC_STATE_SYSTEM_FOCUSABLE
        if self._notebook and self._notebook.HasFocus():
            state |= wx.ACC_STATE_SYSTEM_FOCUSED
        return wx.ACC_OK, state

    def GetChildCount(self):  # noqa: N802
        if not self._notebook:
            return wx.ACC_FAIL, 0
        return wx.ACC_OK, self._notebook.GetPageCount()

    def GetChild(self, childId: int):  # noqa: N802
        if not self._notebook or childId < 1:
            return wx.ACC_INVALID_ARG, None
        idx = childId - 1
        if idx < 0 or idx >= self._notebook.GetPageCount():
            return wx.ACC_INVALID_ARG, None
        return wx.ACC_OK, self._tab(idx)

    def GetFocus(self, *args, **kwargs):  # noqa: N802
        if not self._notebook:
            return wx.ACC_FAIL, None
        idx = self._notebook.GetSelection()
        if idx is None or idx < 0:
            return wx.ACC_FAIL, None
        return wx.ACC_OK, self._tab(idx)


def install_notebook_accessible(notebook: wx.Notebook, *, name: str = "Zakładki") -> None:
    if wx.Platform != "__WXMSW__":
        return
    notebook.SetName(name)

    def install_on(win: wx.Window) -> None:
        existing = win.GetAccessible()
        if isinstance(existing, NotebookAccessible):
            return
        win.SetAccessible(NotebookAccessible(win, notebook, name=name))

    install_on(notebook)

    page_windows = {notebook.GetPage(i) for i in range(notebook.GetPageCount())}
    for child in notebook.GetChildren():
        # Avoid overriding the pages' own accessibility. We only want the
        # notebook/tab-strip windows to expose a clean tab list.
        if child in page_windows:
            continue
        install_on(child)
