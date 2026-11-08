"""Core link extraction and checking for mdlinkcheck."""

import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from fnmatch import fnmatch
from urllib.parse import urlparse

INLINE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
REFERENCE_DEF_RE = re.compile(
    r"^\s*\[[^\]]+\]:\s*(\S+)", re.MULTILINE
)
REFERENCE_USE_RE = re.compile(r"\[[^\]]+\]\[([^\]]*)\]")
BARE_URL_RE = re.compile(r"(?<![(\[\"'=])https?://[^\s)\"'<>`]+")

SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


@dataclass
class LinkResult:
    """One checked link."""

    url: str
    source: str
    status: str  # "ok" | "broken" | "skipped"
    detail: str = ""
    status_code: int | None = None

    def to_dict(self):
        return {
            "url": self.url,
            "source": self.source,
            "status": self.status,
            "detail": self.detail,
            "status_code": self.status_code,
        }


@dataclass
class Report:
    """Aggregate result of a full check run."""

    results: list = field(default_factory=list)
    files_scanned: int = 0
    warnings: list = field(default_factory=list)

    @property
    def broken(self):
        return [r for r in self.results if r.status == "broken"]

    @property
    def ok(self):
        return [r for r in self.results if r.status == "ok"]

    @property
    def skipped(self):
        return [r for r in self.results if r.status == "skipped"]

    def to_dict(self):
        return {
            "files_scanned": self.files_scanned,
            "total_links": len(self.results),
            "ok": len(self.ok),
            "broken": len(self.broken),
            "skipped": len(self.skipped),
            "results": [r.to_dict() for r in self.results],
            "warnings": self.warnings,
        }


def extract_links(text):
    """Extract all links from Markdown text.

    Returns a list of (url, kind) pairs where kind is "url" for
    http(s) links, "anchor" for #fragment links, and "file" for
    relative file paths.
    """
    found = []

    def add(url):
        url = url.strip().strip("<>")
        if not url:
            return
        if url.startswith("#"):
            found.append((url, "anchor"))
        elif url.startswith(("http://", "https://")):
            found.append((url, "url"))
        elif SCHEME_RE.match(url):
            # mailto:, tel:, ftp:, etc. — out of scope.
            return
        else:
            found.append((url, "file"))

    for match in INLINE_LINK_RE.finditer(text):
        add(match.group(1))
    for match in REFERENCE_DEF_RE.finditer(text):
        add(match.group(1))
    # Bare URLs not already covered by an inline link.
    for match in BARE_URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:!?")
        if not any(url == u for u, _ in found):
            found.append((url, "url"))
    return found


def is_excluded(url, patterns):
    """True if the URL matches any glob pattern (substring or fnmatch)."""
    for pattern in patterns:
        if not pattern:
            continue
        if pattern in url or fnmatch(url, pattern):
            return True
    return False


def check_url(url, timeout=10.0):
    """Check one http(s) URL. Returns (status, detail, status_code)."""
    for method in ("HEAD", "GET"):
        request = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                code = response.getcode()
                final = response.geturl()
                detail = f"HTTP {code}"
                if final != url:
                    detail += f" (redirected to {final})"
                return "ok", detail, code
        except urllib.error.HTTPError as exc:
            if exc.code == 405 and method == "HEAD":
                continue  # Fall back to GET.
            if 400 <= exc.code < 500:
                return "broken", f"HTTP {exc.code}", exc.code
            return "broken", f"HTTP {exc.code}", exc.code
        except urllib.error.URLError as exc:
            return "broken", f"unreachable: {exc.reason}", None
        except (TimeoutError, OSError) as exc:
            return "broken", f"error: {exc}", None
    return "broken", "error: request failed", None


def check_file_link(link, source_path):
    """Check a local (relative path or #anchor) link."""
    if link.startswith("#"):
        return "ok", "anchor within same file", None
    path_part = link.split("#", 1)[0]
    if not path_part:
        return "ok", "anchor within same file", None
    target = os.path.normpath(
        os.path.join(os.path.dirname(source_path), path_part)
    )
    if os.path.exists(target):
        return "ok", "file exists", None
    return "broken", "file not found", None


def collect_markdown_files(paths):
    """Yield .md files under the given files/directories."""
    files = []
    for path in paths:
        if os.path.isfile(path):
            if path.endswith(".md"):
                files.append(path)
        elif os.path.isdir(path):
            for root, dirs, names in os.walk(path):
                dirs[:] = sorted(
                    d for d in dirs if not d.startswith(".")
                )
                for name in sorted(names):
                    if name.endswith(".md"):
                        files.append(os.path.join(root, name))
    return files


def check_paths(paths, timeout=10.0, exclude=(), ignore_file=None,
                only_broken=False):
    """Scan paths for Markdown files and check every link found."""
    report = Report()
    patterns = list(exclude or [])
    if ignore_file:
        try:
            with open(ignore_file, encoding="utf-8") as handle:
                patterns.extend(
                    line.strip() for line in handle if line.strip()
                )
        except OSError as exc:
            message = f"could not read ignore file {ignore_file}: {exc}"
            report.warnings.append(message)
            print(f"warning: {message}", file=sys.stderr)

    seen = set()
    for md_file in collect_markdown_files(paths):
        try:
            with open(md_file, encoding="utf-8") as handle:
                text = handle.read()
        except (OSError, UnicodeDecodeError) as exc:
            message = f"could not read {md_file}: {exc}"
            report.warnings.append(message)
            print(f"warning: {message}", file=sys.stderr)
            continue
        report.files_scanned += 1
        for url, kind in extract_links(text):
            if url in seen:
                continue
            seen.add(url)
            if is_excluded(url, patterns):
                report.results.append(
                    LinkResult(url, md_file, "skipped", "excluded")
                )
                continue
            if kind == "url":
                status, detail, code = check_url(url, timeout)
            elif kind == "anchor":
                status, detail, code = check_file_link(url, md_file)
            else:
                status, detail, code = check_file_link(url, md_file)
            report.results.append(
                LinkResult(url, md_file, status, detail, code)
            )
    return report
