"""Local MCP bridge for the graham-daily workspace.

The server communicates over stdio and is intended for Claude Desktop or an
MCP-capable editor. It never returns the contents of secret files.
"""

from __future__ import annotations

import fnmatch
import os
import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP


ROOT = Path(__file__).resolve().parent
MAX_READ_BYTES = 200_000
MAX_OUTPUT_CHARS = 50_000
SECRET_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "credentials.json",
    "secrets.json",
}
SECRET_GLOBS = ("*.env", "*secret*", "*credential*")

mcp = FastMCP("graham-daily-workspace")


def safe_path(relative_path: str) -> Path:
    candidate = (ROOT / relative_path).resolve()
    try:
        candidate.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("הנתיב חייב להישאר בתוך תיקיית הפרויקט") from exc
    if candidate.name.lower() in SECRET_NAMES or any(
        fnmatch.fnmatch(candidate.name.lower(), pattern)
        for pattern in SECRET_GLOBS
    ):
        raise ValueError("גישה לקובצי סודות חסומה")
    return candidate


def clean_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + "\n...[output truncated]"


@mcp.tool()
def list_files(path: str = ".") -> str:
    """List files and directories below a workspace path."""
    folder = safe_path(path)
    if not folder.is_dir():
        raise ValueError("הנתיב אינו תיקייה")
    entries = []
    for item in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if item.name.lower() in SECRET_NAMES or any(
            fnmatch.fnmatch(item.name.lower(), pattern) for pattern in SECRET_GLOBS
        ):
            continue
        entries.append(f"{'[file]' if item.is_file() else '[dir ]'} {item.relative_to(ROOT)}")
    return "\n".join(entries) or "(empty)"


@mcp.tool()
def read_file(path: str) -> str:
    """Read a UTF-8 text file in the workspace; secret files are blocked."""
    file_path = safe_path(path)
    if not file_path.is_file():
        raise ValueError("הקובץ לא נמצא")
    if file_path.stat().st_size > MAX_READ_BYTES:
        raise ValueError(f"הקובץ גדול מדי לקריאה (מקסימום {MAX_READ_BYTES} bytes)")
    return file_path.read_text(encoding="utf-8")


@mcp.tool()
def write_file(path: str, content: str) -> str:
    """Create or replace a UTF-8 text file in the workspace."""
    file_path = safe_path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return f"נכתב: {file_path.relative_to(ROOT)}"


@mcp.tool()
def search_files(query: str, path: str = ".") -> str:
    """Search text files below a workspace path."""
    folder = safe_path(path)
    if not folder.is_dir():
        raise ValueError("הנתיב אינו תיקייה")
    matches = []
    for file_path in folder.rglob("*"):
        if not file_path.is_file():
            continue
        try:
            relative = file_path.relative_to(ROOT)
            if safe_path(str(relative)) != file_path:
                continue
            text = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if query.casefold() in line.casefold():
                matches.append(f"{relative}:{number}: {line}")
    return "\n".join(matches) or "לא נמצאו התאמות"


@mcp.tool()
def run_command(command: str, timeout_seconds: int = 120) -> str:
    """Run a command with the workspace as its working directory."""
    lowered = command.casefold()
    if any(token in lowered for token in (".env", "secret", "credential")):
        raise ValueError("פקודות המתייחסות לסודות חסומות")
    timeout = max(1, min(int(timeout_seconds), 600))
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return f"פקודה הסתיימה ב-timeout אחרי {timeout} שניות\n{clean_output(output)}"
    output = (result.stdout or "") + (result.stderr or "")
    return f"exit_code={result.returncode}\n{clean_output(output)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
