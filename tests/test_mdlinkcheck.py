"""Tests for mdlinkcheck.

All HTTP checks run against a local test server — nothing here touches
the real internet.
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mdlinkcheck.core import (
    check_file_link,
    check_paths,
    check_url,
    collect_markdown_files,
    extract_links,
    is_excluded,
)
from mdlinkcheck.cli import format_text, main


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _route(self):
        if self.path == "/ok":
            return 200, b"fine"
        if self.path == "/missing":
            return 404, b"not here"
        if self.path == "/redirect":
            self.send_response(301)
            self.send_header("Location", "/ok")
            self.end_headers()
            return None, None
        if self.path == "/head405":
            if self.command == "HEAD":
                return 405, b"no head"
            return 200, b"fine via get"
        if self.path == "/slow":
            time.sleep(2)
            return 200, b"slow"
        return 404, b"not here"

    def do_GET(self):
        status, body = self._route()
        if status is None:
            return
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        status, body = self._route()
        if status is None:
            return
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()


@pytest.fixture(scope="module")
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture
def docs(tmp_path):
    (tmp_path / "other.md").write_text("# Other\n")
    (tmp_path / "guide.md").write_text(
        "# Guide\n\n[Home](index.md) [Anchor](#section)\n"
    )
    return tmp_path


def write_md(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


# --- extraction -------------------------------------------------------

def test_extract_inline_link():
    links = extract_links("[Docs](https://example.com/docs)")
    assert ("https://example.com/docs", "url") in links


def test_extract_reference_link():
    text = "See [the guide][g].\n\n[g]: https://example.com/guide\n"
    links = extract_links(text)
    assert ("https://example.com/guide", "url") in links


def test_extract_bare_url():
    links = extract_links("Visit https://example.com for details.")
    assert ("https://example.com", "url") in links


def test_extract_anchor_and_relative_file():
    links = extract_links("[Jump](#top) and [Other](other.md)")
    assert ("#top", "anchor") in links
    assert ("other.md", "file") in links


def test_extract_ignores_mailto():
    links = extract_links("[Mail](mailto:hello@example.com)")
    assert not links


def test_extract_link_with_title():
    links = extract_links('[Docs](https://example.com "title")')
    assert ("https://example.com", "url") in links


# --- HTTP checks (local server) ---------------------------------------

def test_check_url_good_link(server):
    status, detail, code = check_url(f"{server}/ok")
    assert status == "ok"
    assert code == 200


def test_check_url_404_link(server):
    status, detail, code = check_url(f"{server}/missing")
    assert status == "broken"
    assert code == 404


def test_check_url_redirect_followed(server):
    status, detail, code = check_url(f"{server}/redirect")
    assert status == "ok"
    assert code == 200
    assert "redirected" in detail


def test_check_url_head_405_falls_back_to_get(server):
    status, detail, code = check_url(f"{server}/head405")
    assert status == "ok"
    assert code == 200


def test_check_url_timeout_or_unreachable(server):
    # A port on localhost where nothing listens.
    status, detail, code = check_url("http://127.0.0.1:1/", timeout=2)
    assert status == "broken"
    assert code is None


def test_check_url_slow_server_times_out(server):
    status, detail, code = check_url(f"{server}/slow", timeout=0.5)
    assert status == "broken"


# --- local links ------------------------------------------------------

def test_check_file_link_existing(docs):
    status, _, _ = check_file_link("other.md", str(docs / "guide.md"))
    assert status == "ok"


def test_check_file_link_missing(docs):
    status, detail, _ = check_file_link("nope.md", str(docs / "guide.md"))
    assert status == "broken"
    assert "not found" in detail


def test_check_file_link_anchor(docs):
    status, _, _ = check_file_link("#section", str(docs / "guide.md"))
    assert status == "ok"


def test_check_file_link_with_anchor_to_existing_file(docs):
    status, _, _ = check_file_link("other.md#top", str(docs / "guide.md"))
    assert status == "ok"


# --- exclusion --------------------------------------------------------

def test_is_excluded_substring_and_glob():
    assert is_excluded("https://example.com/a", ["example.com"])
    assert is_excluded("https://sub.example.com/a", ["*.example.com/*"])
    assert not is_excluded("https://other.org/a", ["example.com"])


def test_exclude_pattern_skips_link(tmp_path, server):
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok) [Skip]({server}/missing)")
    report = check_paths([str(tmp_path)], exclude=["missing"])
    statuses = {r.url: r.status for r in report.results}
    assert statuses[f"{server}/missing"] == "skipped"
    assert statuses[f"{server}/ok"] == "ok"


def test_ignore_file_skips_urls(tmp_path, server):
    ignore = tmp_path / "ignore.txt"
    ignore.write_text("missing\n")
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok) [Skip]({server}/missing)")
    report = check_paths([str(tmp_path)], ignore_file=str(ignore))
    statuses = {r.url: r.status for r in report.results}
    assert statuses[f"{server}/missing"] == "skipped"


# --- scanning ---------------------------------------------------------

def test_collect_markdown_files_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    write_md(tmp_path, "a.md", "x")
    write_md(sub, "b.md", "x")
    (tmp_path / "notes.txt").write_text("not markdown")
    files = collect_markdown_files([str(tmp_path)])
    assert len(files) == 2
    assert all(f.endswith(".md") for f in files)


def test_check_paths_full_run(tmp_path, server):
    write_md(tmp_path, "other.md", "# Other\n")
    write_md(
        tmp_path, "a.md",
        f"[Ok]({server}/ok)\n[Bad]({server}/missing)\n"
        "[Local](other.md)\n[Missing](gone.md)\n",
    )
    report = check_paths([str(tmp_path)])
    assert report.files_scanned == 2
    assert len(report.broken) == 2
    assert len(report.ok) == 2
    assert report.broken[0].source.endswith("a.md")


def test_check_paths_deduplicates_urls(tmp_path, server):
    write_md(tmp_path, "a.md", f"[A]({server}/ok)\n[B]({server}/ok)\n")
    report = check_paths([str(tmp_path)])
    assert len(report.results) == 1


def test_unreadable_file_warns_and_continues(tmp_path, server, capsys):
    good = write_md(tmp_path, "good.md", f"[Ok]({server}/ok)")
    bad = write_md(tmp_path, "bad.md", "x")
    os.chmod(bad, 0)
    try:
        report = check_paths([str(tmp_path)])
    finally:
        os.chmod(bad, 0o644)
    # Either the file could not be read (warning recorded) or the
    # platform let us read it anyway — in both cases the good file
    # must still have been checked.
    assert any(r.url == f"{server}/ok" for r in report.results)
    if report.warnings:
        assert "bad.md" in report.warnings[0]


def test_missing_ignore_file_warns(tmp_path, capsys):
    write_md(tmp_path, "a.md", "no links")
    report = check_paths([str(tmp_path)], ignore_file=str(tmp_path / "nope.txt"))
    assert report.warnings
    assert "nope.txt" in report.warnings[0]


# --- CLI / output -----------------------------------------------------

def test_cli_exit_code_zero_on_ok(tmp_path, server):
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok)")
    assert main([str(tmp_path)]) == 0


def test_cli_exit_code_one_on_broken(tmp_path, server):
    write_md(tmp_path, "a.md", f"[Bad]({server}/missing)")
    assert main([str(tmp_path)]) == 1


def test_cli_exit_code_two_on_bad_args():
    with pytest.raises(SystemExit) as exc:
        main(["--timeout", "-1", "."])
    assert exc.value.code == 2


def test_cli_json_shape(tmp_path, server, capsys):
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok) [Bad]({server}/missing)")
    assert main(["--json", str(tmp_path)]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["files_scanned"] == 1
    assert data["total_links"] == 2
    assert data["ok"] == 1
    assert data["broken"] == 1
    assert data["results"][0]["url"].startswith("http")
    assert set(data["results"][0]) == {
        "url", "source", "status", "detail", "status_code",
    }


def test_cli_only_broken_hides_ok(tmp_path, server, capsys):
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok) [Bad]({server}/missing)")
    main(["--only-broken", str(tmp_path)])
    out = capsys.readouterr().out
    assert f"{server}/missing" in out
    assert f"{server}/ok" not in out.splitlines()[0]


def test_format_text_marks_broken(tmp_path, server):
    write_md(tmp_path, "a.md", f"[Bad]({server}/missing)")
    report = check_paths([str(tmp_path)])
    text = format_text(report)
    assert "BROKEN" in text
    assert "1 broken link(s) found." in text


def test_module_entry_point(tmp_path, server):
    write_md(tmp_path, "a.md", f"[Ok]({server}/ok)")
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    result = subprocess.run(
        [sys.executable, "-m", "mdlinkcheck", str(tmp_path)],
        capture_output=True, text=True, cwd=project_root,
    )
    assert result.returncode == 0
    assert "OK" in result.stdout
