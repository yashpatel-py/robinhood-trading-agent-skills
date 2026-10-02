#!/usr/bin/env python3
"""Fail the build if shipped code can reach the network.

Usage:
    python3 tools/no_network_check.py [--root DIR] [--json] [PATH ...]

Scanned (when no PATH is given): hooks/, shared/scripts/, skills/*/scripts/, sandbox/,
integrations/, optional/ (if present) and the top-level evalkit/*.py (the sandbox serves
the fixture through evalkit/render.py). Exit 0 when clean, 1 on any finding, 2 on a usage
error.

Why this exists: these files run on the user's machine next to a live brokerage
connection, inside hooks that see every Robinhood tool call. The kit's promise is that
nothing it ships phones home: the audit log stays local, the scripts do arithmetic on
JSON, and the sandbox speaks MCP over stdio. A single `import urllib.request` would break
that promise silently, so CI checks it instead of a reviewer.

What counts as network use:
  Python (parsed with `ast`, so prose in strings and comments never trips it):
    - importing socket, ssl, socketserver, http.client, http.server, http.cookiejar,
      urllib (anything but urllib.parse, which only parses strings), urllib3, requests,
      httpx, aiohttp, websocket, websockets, ftplib, smtplib, poplib, imaplib, telnetlib,
      xmlrpc, webbrowser, paramiko, pycurl, grpc; also via __import__ or
      importlib.import_module with a literal name
    - asyncio.open_connection / start_server / create_connection /
      create_datagram_endpoint / open_unix_connection / start_unix_server
    - a subprocess or os.system/os.popen/os.exec* call whose command names curl, wget,
      nc, ncat, netcat, socat, telnet, ssh, scp, sftp, rsync, ftp or gh
  Shell (.sh, or a file with a sh/bash shebang), comments ignored:
    - running curl, wget, nc, ncat, netcat, socat, telnet, ssh, scp, sftp, rsync or ftp
    - /dev/tcp/ or /dev/udp/ redirections
  JSON (hook and MCP configs): any "command" string that runs one of those programs,
  and any "url"/"httpUrl" inside an "mcpServers" block outside integrations/ (a remote
  server entry is a network dependency; the Robinhood endpoint belongs only in the
  manifests at the repo root, and the sandbox's server entry must be a stdio command).

sandbox/mock_server.py gets no exemption: it must speak MCP over stdio only, so the same
rules cover it.

Stdlib only; Python 3.9+. No network access.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

DEFAULT_ROOT = Path(__file__).resolve().parent.parent

SCAN_DIRS = ("hooks", "shared/scripts", "sandbox", "integrations", "optional")
SCAN_GLOB_DIRS = ("skills/*/scripts",)
SCAN_TOP_LEVEL_PY = ("evalkit",)
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".ruff_cache", ".mypy_cache", ".pytest_cache"}

NETWORK_MODULES = {
    "socket", "ssl", "socketserver", "http.client", "http.server", "http.cookiejar", "urllib", "urllib.request",
    "urllib.error", "urllib.response", "urllib.robotparser", "urllib3", "requests", "httpx", "aiohttp",
    "websocket", "websockets", "ftplib", "smtplib", "poplib", "imaplib", "telnetlib", "xmlrpc", "xmlrpc.client",
    "xmlrpc.server", "webbrowser", "paramiko", "pycurl", "grpc",
}
# Pure string handling inside an otherwise forbidden package.
ALLOWED_MODULES = {"urllib.parse"}
ASYNCIO_NETWORK = {"open_connection", "start_server", "create_connection", "create_datagram_endpoint",
                   "open_unix_connection", "start_unix_server", "create_unix_connection", "create_server"}
NETWORK_PROGRAMS = {"curl", "wget", "nc", "ncat", "netcat", "socat", "telnet", "ssh", "scp", "sftp", "rsync",
                    "ftp", "gh"}
SHELL_NETWORK_PROGRAMS = NETWORK_PROGRAMS - {"gh"}
PROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
OS_PROCESS_FUNCS = {"system", "popen", "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp",
                    "execlpe", "spawnv", "spawnvp", "spawnl", "spawnlp"}

SHELL_COMMAND = re.compile(r"(?:^|[\s;&|(`]|\$\()(?:sudo\s+|command\s+|exec\s+|env\s+)?(?P<prog>(?:[\w./-]*/)?"
                           r"(?:%s))(?=\s|$|[;&|)`])" % "|".join(sorted(SHELL_NETWORK_PROGRAMS, key=len, reverse=True)))
DEV_NET = re.compile(r"/dev/(?:tcp|udp)/")
SHEBANG_SH = re.compile(r"^#!.*\b(?:sh|bash|dash|zsh|ksh)\b")


class Finding(object):
    __slots__ = ("path", "line", "what")

    def __init__(self, path: str, line: int, what: str) -> None:
        self.path = path
        self.line = line
        self.what = what

    def as_dict(self) -> Dict[str, object]:
        return {"path": self.path, "line": self.line, "what": self.what}

    def __str__(self) -> str:
        return "{}:{}: network use: {}".format(self.path, self.line, self.what)


# --------------------------------------------------------------------------- python

def _module_forbidden(name: str) -> bool:
    if name in ALLOWED_MODULES:
        return False
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        prefix = ".".join(parts[:i])
        if prefix in ALLOWED_MODULES:
            return False
        if prefix in NETWORK_MODULES:
            return True
    return False


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return base + "." + node.attr if base else node.attr
    return ""


def _string_values(node: ast.AST) -> Iterable[str]:
    """Literal strings inside a call argument (a string, or a list/tuple of strings)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        yield node.value
    elif isinstance(node, (ast.List, ast.Tuple)):
        for elt in node.elts:
            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                yield elt.value
    elif isinstance(node, ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                yield part.value


def _command_program(strings: Sequence[str]) -> Optional[str]:
    if not strings:
        return None
    first = strings[0].strip()
    if not first:
        return None
    word = first.split()[0]
    base = word.rsplit("/", 1)[-1]
    if base in NETWORK_PROGRAMS:
        return base
    # "sh -c 'curl ...'" style: look for a network program as a command word anywhere
    for s in strings:
        m = SHELL_COMMAND.search(s)
        if m:
            return m.group("prog").rsplit("/", 1)[-1]
    return None


class _PyVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hits: List[tuple] = []
        self.aliases: Dict[str, str] = {}

    def _hit(self, node: ast.AST, what: str) -> None:
        self.hits.append((getattr(node, "lineno", 1), what))

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else \
                alias.name.split(".")[0]
            if _module_forbidden(alias.name):
                self._hit(node, "import {}".format(alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level == 0:
            for alias in node.names:
                full = module + "." + alias.name if module else alias.name
                # `from urllib import parse` is fine; `from urllib import request` and
                # `from socket import *` are not (a star import is judged by its module).
                if _module_forbidden(module if alias.name == "*" else full):
                    self._hit(node, "from {} import {}".format(module, alias.name))
                self.aliases[alias.asname or alias.name] = full
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ASYNCIO_NETWORK:
            base = _dotted(node.value)
            resolved = self.aliases.get(base.split(".")[0], base.split(".")[0]) if base else ""
            if resolved == "asyncio" or base.endswith("loop") or base.startswith("asyncio"):
                self._hit(node, "asyncio {}".format(node.attr))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = _dotted(node.func)
        head = name.split(".")[0] if name else ""
        resolved_head = self.aliases.get(head, head)
        resolved = resolved_head + name[len(head):] if name else ""
        # dynamic imports with a literal module name
        if resolved in ("__import__", "importlib.import_module", "import_module") and node.args:
            for s in _string_values(node.args[0]):
                if _module_forbidden(s):
                    self._hit(node, "dynamic import of {}".format(s))
        # process launches that run a network program
        leaf = resolved.rsplit(".", 1)[-1] if resolved else ""
        is_subprocess = resolved.startswith("subprocess.") and leaf in PROCESS_FUNCS
        is_os = resolved.startswith("os.") and leaf in OS_PROCESS_FUNCS
        if is_subprocess or is_os:
            strings: List[str] = []
            for arg in list(node.args) + [kw.value for kw in node.keywords if kw.arg in ("args", "command")]:
                strings.extend(_string_values(arg))
            prog = _command_program(strings)
            if prog:
                self._hit(node, "{}(...) runs {}".format(resolved, prog))
        self.generic_visit(node)


def scan_python(rel: str, text: str) -> List[Finding]:
    try:
        tree = ast.parse(text, filename=rel)
    except SyntaxError as exc:
        return [Finding(rel, exc.lineno or 1, "cannot parse this file, so it cannot be checked ({})".format(exc.msg))]
    visitor = _PyVisitor()
    visitor.visit(tree)
    return [Finding(rel, line, what) for line, what in sorted(set(visitor.hits))]


# --------------------------------------------------------------------------- shell and json

def _strip_shell_comment(line: str) -> str:
    """Drop a trailing # comment that is not inside quotes (good enough for hook scripts)."""
    out = []
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t;"):
            break
        out.append(ch)
    return "".join(out)


def _single_quoted_spans_removed(line: str) -> str:
    # Text inside single quotes is a literal message in these scripts, never a command.
    return re.sub(r"'[^']*'", "''", line)


def scan_shell(rel: str, text: str) -> List[Finding]:
    findings: List[Finding] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = _single_quoted_spans_removed(_strip_shell_comment(raw))
        m = SHELL_COMMAND.search(line)
        if m:
            findings.append(Finding(rel, number, "runs {}".format(m.group("prog").rsplit("/", 1)[-1])))
        if DEV_NET.search(line):
            findings.append(Finding(rel, number, "/dev/tcp or /dev/udp redirection"))
    return findings


def _json_line_of(text: str, needle: str) -> int:
    idx = text.find(needle)
    return text.count("\n", 0, idx) + 1 if idx >= 0 else 1


def scan_json(rel: str, text: str) -> List[Finding]:
    try:
        data = json.loads(text)
    except ValueError:
        return []  # not JSON config (data files are out of scope)
    findings: List[Finding] = []
    allow_urls = rel.startswith("integrations/")

    def walk(node: object, in_servers: bool) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "command" and isinstance(value, str):
                    prog = _command_program([value])
                    if prog:
                        findings.append(Finding(rel, _json_line_of(text, value), "hook command runs {}".format(prog)))
                if in_servers and key in ("url", "httpUrl") and isinstance(value, str) and not allow_urls:
                    if re.match(r"^(?:https?|wss?)://", value):
                        findings.append(Finding(rel, _json_line_of(text, value), "MCP server url {}".format(value)))
                walk(value, in_servers or key == "mcpServers")
        elif isinstance(node, list):
            for item in node:
                walk(item, in_servers)

    walk(data, False)
    return findings


# --------------------------------------------------------------------------- driver

def _is_shell(rel: str, text: str) -> bool:
    if rel.endswith(".sh"):
        return True
    first = text.split("\n", 1)[0]
    return bool(SHEBANG_SH.match(first))


def scan_file(rel: str, text: str) -> List[Finding]:
    if rel.endswith(".py"):
        return scan_python(rel, text)
    if rel.endswith(".json"):
        return scan_json(rel, text)
    if _is_shell(rel, text):
        return scan_shell(rel, text)
    return []


def default_files(root: Path) -> List[str]:
    rels: List[str] = []

    def add_tree(base: Path) -> None:
        if not base.is_dir():
            return
        for dirpath, dirnames, filenames in os.walk(str(base), followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if full.is_symlink():
                    continue
                rels.append(full.relative_to(root).as_posix())

    for d in SCAN_DIRS:
        add_tree(root / d)
    for pattern in SCAN_GLOB_DIRS:
        for base in sorted(root.glob(pattern)):
            add_tree(base)
    for d in SCAN_TOP_LEVEL_PY:
        base = root / d
        if base.is_dir():
            rels.extend(p.relative_to(root).as_posix() for p in sorted(base.glob("*.py")) if not p.is_symlink())
    return sorted(set(rels))


def scan(root: Path, paths: Optional[Sequence[str]] = None) -> List[Finding]:
    root = root.resolve()
    if paths:
        rels = []
        for p in paths:
            full = Path(p)
            full = full if full.is_absolute() else Path.cwd() / full
            try:
                rels.append(full.resolve().relative_to(root).as_posix())
            except ValueError:
                rels.append(full.as_posix())
    else:
        rels = default_files(root)
    findings: List[Finding] = []
    for rel in rels:
        full = root / rel
        if not full.is_file():
            continue
        try:
            data = full.read_bytes()
        except OSError as exc:
            findings.append(Finding(rel, 1, "unreadable, so it cannot be checked ({})".format(exc)))
            continue
        if b"\0" in data[:8192]:
            continue
        findings.extend(scan_file(rel, data.decode("utf-8", "replace")))
    return findings


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Fail if shipped code can reach the network.")
    parser.add_argument("paths", nargs="*", help="files to check (default: the shipped-code directories)")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="repository root (default: %(default)s)")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print("no_network_check: --root {} is not a directory".format(root), file=sys.stderr)
        return 2
    findings = scan(root, args.paths or None)
    if args.json:
        print(json.dumps({"ok": not findings, "findings": [f.as_dict() for f in findings]}, indent=2))
    else:
        for f in findings:
            print(f)
        if findings:
            print("no_network_check: {} finding(s). Shipped code must not reach the network; the sandbox speaks "
                  "MCP over stdio only.".format(len(findings)), file=sys.stderr)
        else:
            print("no_network_check: clean")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
