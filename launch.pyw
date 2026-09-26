"""
הפעלת מסך המסחר בלי חלון שחור.

קיצור הדרך מריץ את הקובץ הזה עם pythonw, שאין לו חלון. הוא:
  1. עוצר מופע קודם שמאזין לפורט 5000
  2. מפעיל את app.py --lan ברקע, בלי חלון, ומפנה את הפלט ל-app.log
  3. מחכה שהשרת יענה ופותח את הדפדפן
  4. אם השרת לא עלה תוך 20 שניות - מציג הודעה עם סוף הלוג

עצירה:  pythonw launch.pyw --stop   (קיצור הדרך "עצירת מסך המסחר")

app.log מכיל את הקישור לטלפון עם הטוקן, ולכן הוא ב-.gitignore.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = 5000
LOG = HERE / "app.log"
NO_WINDOW = 0x08000000          # CREATE_NO_WINDOW
TITLE = "מסך המסחר"


def msg(text: str, error: bool = False) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, TITLE, 0x10 if error else 0x40)


def listening_pids() -> set:
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         creationflags=NO_WINDOW).stdout
    pids = set()
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 5 and p[0] == "TCP" and p[1].endswith(f":{PORT}") and p[3] == "LISTENING":
            pids.add(p[4])
    return pids


def stop() -> int:
    pids = listening_pids()
    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True,
                       creationflags=NO_WINDOW)
    return len(pids)


def is_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def start() -> None:
    stop()
    for _ in range(10):                      # לחכות שהפורט ישתחרר
        if not is_up():
            break
        time.sleep(0.3)

    # python.exe (לא pythonw) כדי שהפלט ייכתב ללוג; CREATE_NO_WINDOW מסתיר אותו.
    py = Path(sys.executable).with_name("python.exe")
    if not py.exists():
        py = Path(sys.executable)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    with LOG.open("w", encoding="utf-8") as log:
        subprocess.Popen([str(py), "app.py", "--lan"], cwd=HERE, env=env,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                         creationflags=NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)

    for _ in range(40):
        if is_up():
            webbrowser.open(f"http://127.0.0.1:{PORT}/")
            return
        time.sleep(0.5)
    try:
        tail = LOG.read_text(encoding="utf-8", errors="replace")[-1500:]
    except OSError:
        tail = ""
    msg("המסך לא עלה תוך 20 שניות.\n\n" + tail, error=True)


def main() -> None:
    if "--stop" in sys.argv:
        n = stop()
        msg("המסך נעצר." if n else "המסך לא רץ.")
    else:
        start()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pythonw בולע שגיאות; לפחות להראות אותן
        msg(f"שגיאה בהפעלה: {type(exc).__name__}: {exc}", error=True)
