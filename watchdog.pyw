"""
שומר על מסך המסחר: משימה מתוזמנת ב-Windows מריצה את הקובץ הזה כל 5 דקות (pythonw, בלי חלון).

בודק שהשרת עונה ל-HTTP במחשב עצמו (127.0.0.1). אם אין תשובה בשני ניסיונות,
שומר את סוף הלוג הקודם ל-app.prev.log ומפעיל את המסך מחדש (launch.start, בלי דפדפן).
אחרי עלייה המסך עצמו שולח לטלגרם הודעה עם קישור חדש, כך שרואים שהיה נפילה.

הרישום (פעם אחת):   python watchdog.pyw --install
הסרה:                pythonw watchdog.pyw --uninstall
יומן:                %LOCALAPPDATA%\\graham-daily\\watchdog.log
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASK = "graham-daily watchdog"
COOLDOWN = 240           # שניות: לא להפעיל שוב אם הפעלנו לפני פחות מזה (הנפקת תעודה איטית)


def _launch():
    loader = importlib.machinery.SourceFileLoader("launch", str(HERE / "launch.pyw"))
    spec = importlib.util.spec_from_loader("launch", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


L = _launch()
LOGDIR = L.LOG.parent
WLOG = LOGDIR / "watchdog.log"
STAMP = LOGDIR / "watchdog.last"


def log(text: str) -> None:
    try:
        with WLOG.open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
        lines = WLOG.read_text(encoding="utf-8").splitlines()
        if len(lines) > 2000:
            WLOG.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def alive(port: int) -> bool:
    """כל תשובת HTTP (גם שגיאה) = השרת חי. רק חוסר תשובה נחשב נפילה."""
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=30)
        return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError):
        return False


def recently_started() -> bool:
    try:
        return time.time() - float(STAMP.read_text()) < COOLDOWN
    except (OSError, ValueError):
        return False


def check() -> None:
    port = L.LOCAL_PORT if L.public_mode() else L.PORT
    if alive(port):
        return
    time.sleep(20)
    if alive(port) or recently_started():
        return
    try:
        tail = L.LOG.read_text(encoding="utf-8", errors="replace")[-20000:]
        (LOGDIR / "app.prev.log").write_text(tail, encoding="utf-8")
    except OSError:
        pass
    STAMP.write_text(str(time.time()))
    log("המסך לא ענה. מפעיל מחדש...")
    ok = L.start(open_browser=False)
    log("המסך לא ענה. הופעל מחדש: " + ("עלה" if ok else "לא עלה תוך דקה (סוף הלוג הקודם ב-app.prev.log)"))


def install() -> None:
    pyw = Path(sys.executable).with_name("pythonw.exe")
    cmd = f'"{pyw}" "{Path(__file__).resolve()}"'
    r = subprocess.run(["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", "5", "/TN", TASK,
                        "/TR", cmd], capture_output=True, text=True)
    text = ("נרשמה משימה שבודקת את המסך כל 5 דקות." if r.returncode == 0
            else f"הרישום נכשל: {r.stdout} {r.stderr}")
    log(text)
    print(text)


def uninstall() -> None:
    subprocess.run(["schtasks", "/Delete", "/F", "/TN", TASK], capture_output=True)


if __name__ == "__main__":
    try:
        if "--install" in sys.argv:
            install()
        elif "--uninstall" in sys.argv:
            uninstall()
        else:
            check()
    except BaseException as exc:  # noqa: BLE001 - pythonw בולע שגיאות; לפחות ליומן
        import traceback
        log(f"שגיאה: {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
