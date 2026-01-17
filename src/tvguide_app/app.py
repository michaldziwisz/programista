from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path
import sys

# Provider packs are downloaded at runtime and may import parts of stdlib that
# are not referenced by the core app. PyInstaller only bundles modules it sees
# during analysis, so we import required stdlib modules here to avoid crashes.
# (E.g. Puls EPG provider uses xml.etree.ElementTree.)
import xml.etree.ElementTree  # noqa: F401


def _configure_logging() -> Path | None:
    level_name = os.environ.get("LOGLEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    try:
        from platformdirs import user_cache_dir

        logs_dir = Path(user_cache_dir("Programista", "Programista")) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=level)
        return None

    try:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = logs_dir / f"programista-{timestamp}.log"

        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        logging.basicConfig(level=level, handlers=[file_handler, stream_handler])
        logging.getLogger(__name__).info("Writing log to %s", log_path)
        return log_path
    except Exception:  # noqa: BLE001
        logging.basicConfig(level=level)
        return None


def _enable_windows_dpi_awareness() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        try:
            fn = user32.SetProcessDpiAwarenessContext
            fn.argtypes = [ctypes.c_void_p]
            fn.restype = ctypes.c_bool
            # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
            if fn(ctypes.c_void_p(-4)):
                return
        except Exception:
            pass

        try:
            shcore = ctypes.windll.shcore
            fn2 = shcore.SetProcessDpiAwareness
            fn2.argtypes = [ctypes.c_int]
            fn2.restype = ctypes.c_long
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            fn2(2)
            return
        except Exception:
            pass

        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    except Exception:
        return


_enable_windows_dpi_awareness()
_configure_logging()

import wx  # noqa: E402

from tvguide_app.gui.main_frame import MainFrame  # noqa: E402


class ProgramistaApp(wx.App):
    def OnInit(self) -> bool:  # noqa: N802 (wxPython naming)
        frame = MainFrame()
        self.SetTopWindow(frame)
        frame.Show(True)
        return True


def main() -> int:
    app = ProgramistaApp()
    app.MainLoop()
    return 0
