from __future__ import annotations

import platform


def is_packaged_app() -> bool:
    """
    Returns True when running inside an MSIX/AppX package (has package identity).

    This is useful to detect Microsoft Store distribution, where self-updating via
    external installers should usually be disabled in favor of Store updates.
    """

    if platform.system().lower() != "windows":
        return False

    try:
        import ctypes

        get_full_name = ctypes.windll.kernel32.GetCurrentPackageFullName
    except Exception:  # noqa: BLE001
        return False

    # https://learn.microsoft.com/windows/win32/api/appmodel/nf-appmodel-getcurrentpackagefullname
    APPMODEL_ERROR_NO_PACKAGE = 15700
    ERROR_INSUFFICIENT_BUFFER = 122

    length = ctypes.c_uint32(0)
    rc = int(get_full_name(ctypes.byref(length), None))
    if rc == APPMODEL_ERROR_NO_PACKAGE:
        return False
    if rc == 0:
        return True
    if rc != ERROR_INSUFFICIENT_BUFFER:
        return False

    buf = ctypes.create_unicode_buffer(length.value)
    rc = int(get_full_name(ctypes.byref(length), buf))
    return rc == 0

