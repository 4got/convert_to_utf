#!/usr/bin/env python3
"""Copy a site tree to its .utf sibling, converting CP1251 text files to UTF-8.

Does not modify the source tree.

Skip:
  - paths matching .gitignore (`git check-ignore --no-index`)
  - images and other binary extensions
  - files that already decode as UTF-8

Path resolution (highest priority first):
  1. CLI flags: --src, --dst, --log
  2. Environment: SOURCE_PATH, DEST_PATH, LOG_PATH
  3. .env next to this script (same names, e.g. SOURCE_PATH="/path/to/site")
  4. Auto from script location <site_root>/py/convert_to_utf/convert.py:
     src=<site_root>, dst=<site_root>.utf, log=<script_dir>/convert.log

Usage:
  python3 py/convert_to_utf/convert.py --dry-run
  python3 py/convert_to_utf/convert.py --folder about --dry-run
  python3 py/convert_to_utf/convert.py --folder about
  python3 py/convert_to_utf/convert.py --src /path/to/site --dst /path/to/site.utf
  python3 py/convert_to_utf/convert.py
"""

from __future__ import annotations

import argparse
import csv
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import chardet
except ImportError:
    chardet = None

SCRIPT_DIR = Path(__file__).resolve().parent

# Auto defaults from script location: <site_root>/py/convert_to_utf/convert.py
SRC_AUTO = SCRIPT_DIR.parent.parent
DST_AUTO = SRC_AUTO.with_name(SRC_AUTO.name + ".utf")
LOG_AUTO = SCRIPT_DIR / "convert.log"

DOTENV_PATH = SCRIPT_DIR / ".env"
ENV_SRC = "SOURCE_PATH"
ENV_DST = "DEST_PATH"
ENV_LOG = "LOG_PATH"

IMAGE_EXTS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tif",
    ".tiff",
    ".avif",
    ".heic",
}

BINARY_EXTS = {
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".pdf",
    ".zip",
    ".7z",
    ".rar",
    ".gz",
    ".tar",
    ".mp3",
    ".mp4",
    ".webm",
    ".swf",
    ".exe",
    ".dll",
    ".so",
    ".bin",
    ".dat",
}

SKIP_DIR_NAMES = {".git", ".svn", "__pycache__"}
GIT_BATCH = 400


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def fs_bytes(text: str) -> bytes:
    return text.encode("utf-8", "surrogateescape")


def safe_text(text: str) -> str:
    raw = fs_bytes(text)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", "backslashreplace")


def git_ignored_map(repo: Path, rel_paths: Sequence[str]) -> Dict[str, bool]:
    result = {p: False for p in rel_paths}
    if not rel_paths:
        return result
    stdin_data = b"\n".join(fs_bytes(p) for p in rel_paths) + b"\n"
    proc = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", "--stdin"],
        input=stdin_data,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode not in (0, 1):
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError("git check-ignore failed: {}".format(err or proc.returncode))
    ignored_set = {
        line.decode("utf-8", "surrogateescape").strip()
        for line in proc.stdout.splitlines()
    }
    for path in rel_paths:
        if path in ignored_set:
            result[path] = True
    return result


def git_ignored_map_safe(repo: Path, rel_paths: Sequence[str]) -> Dict[str, bool]:
    try:
        return git_ignored_map(repo, rel_paths)
    except Exception:
        result = {}
        for path in rel_paths:
            try:
                result.update(git_ignored_map(repo, [path]))
            except Exception:
                result[path] = False
        return result


def batched(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def detect_action(data: bytes, ext: str) -> Tuple[str, str]:
    """Return (action, encoding). action: convert|copy|skip-binary|skip-utf8."""
    if ext in IMAGE_EXTS or ext in BINARY_EXTS:
        return "skip-binary", "binary"
    if not data:
        return "copy", "empty"
    try:
        data.decode("utf-8")
        return "skip-utf8", "utf-8"
    except UnicodeDecodeError:
        pass

    guessed = ""
    if chardet is not None:
        info = chardet.detect(data)
        guessed = (info.get("encoding") or "").lower().replace("_", "-")

    try:
        data.decode("cp1251")
    except UnicodeDecodeError:
        return "copy", guessed or "unknown"

    return "convert", guessed or "cp1251"


def convert_bytes(data: bytes) -> bytes:
    return data.decode("cp1251").encode("utf-8")


def collect_paths(src: Path, folder: Optional[str]) -> Tuple[Path, List[Path], List[Path]]:
    start = src / folder if folder else src
    if not start.exists():
        raise SystemExit("path not found: {}".format(start))
    dirs: List[Path] = []
    files: List[Path] = []
    if start.is_file():
        files.append(start)
        return start, dirs, files
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        current = Path(dirpath)
        dirs.append(current)
        for name in filenames:
            files.append(current / name)
    return start, dirs, files


def write_log(log_path: Path, rows: List[dict]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", newline="", errors="replace") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["action", "encoding", "relpath", "bytes"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "action": safe_text(str(row.get("action", ""))),
                    "encoding": safe_text(str(row.get("encoding", ""))),
                    "relpath": safe_text(str(row.get("relpath", ""))),
                    "bytes": row.get("bytes", 0),
                }
            )


def load_dotenv(dotenv_path: Path) -> Dict[str, str]:
    """Minimal .env reader: KEY=VALUE lines, # comments, optional quotes."""
    values: Dict[str, str] = {}
    try:
        text = dotenv_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def resolve_path(
    cli_value: Optional[Path],
    env_name: str,
    file_env: Dict[str, str],
    default: Path,
) -> Path:
    """Priority: CLI param -> process env -> .env file -> auto default."""
    if cli_value is not None:
        return Path(cli_value).expanduser()
    for candidate in (os.environ.get(env_name), file_env.get(env_name)):
        if candidate and candidate.strip():
            return Path(candidate.strip()).expanduser()
    return default


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy site tree to dest, converting CP1251 text files to UTF-8"
    )
    parser.add_argument(
        "--src",
        type=Path,
        default=None,
        help="Source site root (default: SOURCE_PATH env/.env, else auto from script location)",
    )
    parser.add_argument(
        "--dst",
        type=Path,
        default=None,
        help="Destination root (default: DEST_PATH env/.env, else <src>.utf)",
    )
    parser.add_argument(
        "--folder",
        help="Test one relative folder or file, e.g. about or local/php_interface",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="CSV log path (default: LOG_PATH env/.env, else <script_dir>/convert.log)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    file_env = load_dotenv(DOTENV_PATH)
    src = resolve_path(args.src, ENV_SRC, file_env, SRC_AUTO).resolve()
    dst = resolve_path(args.dst, ENV_DST, file_env, DST_AUTO).resolve()
    log_path = resolve_path(args.log, ENV_LOG, file_env, LOG_AUTO)
    folder = args.folder.strip("/ ") if args.folder else None

    start, dirs, files = collect_paths(src, folder)
    rel_candidates = []
    for path in dirs + files:
        rel = rel_posix(path, src)
        rel_candidates.append(rel)
        if path.is_dir():
            rel_candidates.append(rel + "/")

    ignored = {}
    for chunk in batched(rel_candidates, GIT_BATCH):
        ignored.update(git_ignored_map_safe(src, chunk))

    def is_ignored(path: Path) -> bool:
        rel = rel_posix(path, src)
        if ignored.get(rel) or ignored.get(rel + "/"):
            return True
        for parent in path.relative_to(src).parents:
            p = parent.as_posix()
            if p == ".":
                continue
            if ignored.get(p) or ignored.get(p + "/"):
                return True
        return False

    rows = []
    counts = {
        "convert": 0,
        "copy": 0,
        "skip-utf8": 0,
        "skip-binary": 0,
        "skip-gitignore": 0,
        "error": 0,
    }

    for path in files:
        rel = ""
        data = b""
        try:
            rel = rel_posix(path, src)
            if is_ignored(path):
                counts["skip-gitignore"] += 1
                rows.append(
                    {
                        "action": "skip-gitignore",
                        "encoding": "",
                        "relpath": rel,
                        "bytes": path.stat().st_size,
                    }
                )
                continue
            data = path.read_bytes()
            action, encoding = detect_action(data, path.suffix.lower())
            dest_path = dst / rel
            if not args.dry_run:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                if action == "convert":
                    dest_path.write_bytes(convert_bytes(data))
                    shutil.copystat(path, dest_path, follow_symlinks=True)
                else:
                    shutil.copy2(path, dest_path)
            counts[action] = counts.get(action, 0) + 1
            rows.append(
                {
                    "action": action,
                    "encoding": encoding,
                    "relpath": rel,
                    "bytes": len(data),
                }
            )
        except Exception as exc:
            counts["error"] += 1
            rows.append(
                {
                    "action": "error",
                    "encoding": safe_text(str(exc)),
                    "relpath": rel or safe_text(str(path)),
                    "bytes": len(data),
                }
            )

    write_log(log_path, rows)
    mode = "dry-run" if args.dry_run else "write"
    scope = folder or "."
    print("mode={} src={} dst={} folder={}".format(mode, src, dst, scope))
    print("files_seen={} log={}".format(len(files), log_path))
    for key in ("convert", "copy", "skip-utf8", "skip-binary", "skip-gitignore", "error"):
        print("{}={}".format(key, counts.get(key, 0)))
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        sys.stderr.write("fatal: {}\n".format(safe_text(str(exc))))
        sys.exit(1)
