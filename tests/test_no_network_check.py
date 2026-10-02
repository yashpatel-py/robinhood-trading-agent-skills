"""Tests for tools/no_network_check.py.

Run: python3 -m unittest discover -s tests -v
"""

import contextlib
import io
import json
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import no_network_check as nn  # noqa: E402


def py(source):
    return [f.what for f in nn.scan_python("x.py", textwrap.dedent(source))]


class PythonRules(unittest.TestCase):
    def test_network_imports_fail(self):
        self.assertEqual(py("import socket\n"), ["import socket"])
        self.assertEqual(py("import urllib.request as r\n"), ["import urllib.request"])
        self.assertEqual(py("import urllib\n"), ["import urllib"])
        self.assertEqual(py("from urllib import request\n"), ["from urllib import request"])
        self.assertEqual(py("from http import client\n"), ["from http import client"])
        self.assertEqual(py("from http.server import HTTPServer\n"), ["from http.server import HTTPServer"])
        self.assertEqual(py("import requests\n"), ["import requests"])
        self.assertEqual(py("from socket import *\n"), ["from socket import *"])
        self.assertEqual(py("import ssl, json\n"), ["import ssl"])
        self.assertEqual(py("def f():\n    import http.client\n"), ["import http.client"])

    def test_harmless_imports_pass(self):
        self.assertEqual(py("import urllib.parse\nfrom urllib.parse import urlparse, parse_qs\n"
                            "from urllib import parse\nfrom http import HTTPStatus\nimport json, subprocess\n"), [])

    def test_dynamic_imports_fail(self):
        self.assertEqual(py("__import__('socket')\n"), ["dynamic import of socket"])
        self.assertEqual(py("import importlib\nimportlib.import_module('http.client')\n"),
                         ["dynamic import of http.client"])
        self.assertEqual(py("import importlib\nimportlib.import_module('json')\n"), [])

    def test_process_calls_that_run_network_programs_fail(self):
        self.assertEqual(py("import subprocess\nsubprocess.run(['curl', '-s', 'https://x'])\n"),
                         ["subprocess.run(...) runs curl"])
        self.assertEqual(py("import subprocess as sp\nsp.check_output('wget -q x', shell=True)\n"),
                         ["subprocess.check_output(...) runs wget"])
        self.assertEqual(py("from subprocess import Popen as P\nP(['/usr/bin/nc', 'host', '80'])\n"),
                         ["subprocess.Popen(...) runs nc"])
        self.assertEqual(py("import os\nos.system('curl https://x')\n"), ["os.system(...) runs curl"])
        self.assertEqual(py("import subprocess\nsubprocess.run(['sh', '-c', 'echo hi; curl x'])\n"),
                         ["subprocess.run(...) runs curl"])
        self.assertEqual(py("import subprocess\nsubprocess.run(args=['gh', 'api', 'x'])\n"),
                         ["subprocess.run(...) runs gh"])

    def test_local_process_calls_pass(self):
        self.assertEqual(py("import subprocess, sys\nsubprocess.run([sys.executable, 'script.py'])\n"
                            "subprocess.run(['sh', 'hooks/guard.sh', 'money'])\n"
                            "subprocess.run(['git', 'ls-files'])\n"), [])

    def test_asyncio_network_fails_stdio_passes(self):
        self.assertEqual(py("import asyncio\nasyncio.open_connection('h', 1)\n"), ["asyncio open_connection"])
        self.assertEqual(py("import asyncio\nasync def m(loop):\n    await loop.create_connection(f, 'h', 1)\n"),
                         ["asyncio create_connection"])
        self.assertEqual(py("import asyncio, sys\nasyncio.run(main())\nsys.stdin.readline()\n"), [])

    def test_prose_never_trips(self):
        self.assertEqual(py('"""Use curl or requests to fetch; import socket is banned."""\n'
                            "# import socket\nMESSAGE = 'queued exercise requests'\n"), [])

    def test_unparseable_file_is_reported(self):
        whats = py("def broken(:\n")
        self.assertEqual(len(whats), 1)
        self.assertIn("cannot parse", whats[0])


class ShellAndJsonRules(unittest.TestCase):
    def sh(self, text, rel="hooks/x.sh"):
        return [f.what for f in nn.scan_file(rel, textwrap.dedent(text))]

    def test_shell_network_commands_fail(self):
        self.assertEqual(self.sh("curl -s https://x\n"), ["runs curl"])
        self.assertEqual(self.sh("out=$(wget -qO- x)\n"), ["runs wget"])
        self.assertEqual(self.sh("printf x | nc host 80\n"), ["runs nc"])
        self.assertEqual(self.sh("exec 3<>/dev/tcp/example.invalid/80\n"), ["/dev/tcp or /dev/udp redirection"])
        self.assertEqual(self.sh("command curl x\n"), ["runs curl"])

    def test_shell_prose_and_comments_pass(self):
        self.assertEqual(self.sh("# we never curl anything\nMSG='cancels ALL queued exercise requests; curl'\n"
                                 "printf '%s' \"$MSG\"  # no wget here\n"), [])

    def test_shebang_file_without_extension_is_shell(self):
        self.assertEqual(self.sh("#!/bin/sh\ncurl x\n", rel="hooks/guard"), ["runs curl"])
        self.assertEqual(self.sh("plain text mentioning curl x\n", rel="hooks/NOTES"), [])

    def test_json_hook_commands(self):
        bad = json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "curl -d @- x"}]}]}})
        good = json.dumps({"hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": "sh \"${CLAUDE_PLUGIN_ROOT}/hooks/guard.sh\" money"}]}]}})
        self.assertEqual(self.sh(bad, rel="hooks/hooks.json"), ["hook command runs curl"])
        self.assertEqual(self.sh(good, rel="hooks/hooks.json"), [])

    def test_json_server_urls(self):
        remote = json.dumps({"mcpServers": {"x": {"type": "http", "url": "https://example.invalid/mcp"}}})
        stdio = json.dumps({"mcpServers": {"rh-sandbox": {"command": "python3", "args": ["sandbox/mock_server.py"]}}})
        event = json.dumps({"server_url": "https://example.invalid/mcp", "url": "https://example.invalid/mcp"})
        self.assertEqual(self.sh(remote, rel="sandbox/mcp.json"), ["MCP server url https://example.invalid/mcp"])
        self.assertEqual(self.sh(remote, rel="integrations/x/mcp.json"), [])
        self.assertEqual(self.sh(stdio, rel="sandbox/mcp.json"), [])
        self.assertEqual(self.sh(event, rel="hooks/tests/events/cursor_read.json"), [])

    def test_other_files_are_not_scanned(self):
        self.assertEqual(self.sh("run curl x\n", rel="sandbox/README.md"), [])


class DriverTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="no-net-"))
        self.addCleanup(shutil.rmtree, str(self.root), True)

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def run_main(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = nn.main(args)
        return code, out.getvalue(), err.getvalue()

    def test_default_scope(self):
        for rel in ("hooks/audit_log.py", "hooks/tests/test_x.py", "shared/scripts/rh_time.py",
                    "skills/robinhood-trading/scripts/doctor.py", "sandbox/mock_server.py", "sandbox/mcp.json",
                    "integrations/codex/hooks.json", "evalkit/render.py", "optional/confirm-mode/gate.py",
                    "evalkit/templates/S1/prompt.md.tmpl", "skills/robinhood-trading/SKILL.md", "tools/x.py",
                    "tests/test_y.py", "hooks/__pycache__/audit_log.cpython-313.pyc"):
            self.write(rel, "x = 1\n")
        files = nn.default_files(self.root)
        self.assertEqual(files, sorted([
            "evalkit/render.py", "hooks/audit_log.py", "hooks/tests/test_x.py", "integrations/codex/hooks.json",
            "optional/confirm-mode/gate.py", "sandbox/mcp.json", "sandbox/mock_server.py",
            "shared/scripts/rh_time.py", "skills/robinhood-trading/scripts/doctor.py"]))

    def test_main_exit_codes(self):
        self.write("sandbox/mock_server.py", "import sys\nprint(sys.stdin.readline())\n")
        code, out, _ = self.run_main(["--root", str(self.root)])
        self.assertEqual((code, out.strip()), (0, "no_network_check: clean"))
        self.write("sandbox/mock_server.py", "import http.server\n")
        code, out, err = self.run_main(["--root", str(self.root)])
        self.assertEqual(code, 1)
        self.assertIn("sandbox/mock_server.py:1: network use: import http.server", out)
        code, out, _ = self.run_main(["--root", str(self.root), "--json"])
        self.assertEqual(json.loads(out)["findings"][0]["path"], "sandbox/mock_server.py")
        code, _, err = self.run_main(["--root", str(self.root / "nope")])
        self.assertEqual(code, 2)

    def test_explicit_paths(self):
        a = self.write("tools/a.py", "import socket\n")
        self.write("tools/b.py", "import requests\n")
        findings = nn.scan(self.root, [str(a)])
        self.assertEqual([(f.path, f.what) for f in findings], [("tools/a.py", "import socket")])


if __name__ == "__main__":
    unittest.main()
