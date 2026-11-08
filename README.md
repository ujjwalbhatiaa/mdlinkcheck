# mdlinkcheck

A dead-link checker for Markdown files. Point it at a file or a directory and it
finds every link, checks it, and tells you which ones are broken.

It checks:

- **HTTP(S) links** — inline `[text](url)`, reference-style links, and bare URLs.
  A `HEAD` request is tried first, falling back to `GET` when a server rejects
  `HEAD` (HTTP 405). Redirects are followed.
- **Local links** — `#anchors` inside the same file and relative paths to other
  files, which are checked for existence on disk.

Pure Python, standard library only — no dependencies to install.

## Install

```bash
pip install .
```

## Usage

```bash
# Check a single file
mdlinkcheck README.md

# Check a whole directory (recursively)
mdlinkcheck docs/

# Show only broken links
mdlinkcheck --only-broken docs/

# Skip URLs matching a pattern, and set a shorter timeout
mdlinkcheck --exclude "example.com/*" --timeout 5 docs/

# Skip URLs listed in a file (one substring per line)
mdlinkcheck --ignore-file .linkignore docs/

# Machine-readable output
mdlinkcheck --json docs/
```

## Example output

```
$ mdlinkcheck docs/
OK     https://example.com/guide  (docs/index.md) — HTTP 200
OK     https://example.com/old-page  (docs/index.md) — HTTP 200 (redirected to https://example.com/new-page)
BROKEN https://example.com/missing  (docs/index.md) — HTTP 404
BROKEN ./no-such-file.md  (docs/index.md) — file not found
SKIP   https://internal.example.com/  (docs/index.md) — excluded

1 file(s) scanned, 5 link(s): 2 ok, 2 broken, 1 skipped.
2 broken link(s) found.
```

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | All links OK (or skipped) |
| 1 | Broken links found |
| 2 | Usage error (bad arguments) |

## Options

| Flag | Description |
| ---- | ----------- |
| `--timeout SECONDS` | Seconds to wait per HTTP request (default: 10) |
| `--exclude PATTERN` | Glob pattern or substring of URLs to skip (repeatable) |
| `--ignore-file FILE` | File with one URL substring per line to skip |
| `--json` | Print the report as JSON |
| `--only-broken` | Show only broken links in the text report |

Unreadable files and network errors never crash a run: a warning is printed
to stderr and checking continues with the remaining links.

## Running the tests

```bash
pip install pytest
pytest
```

## License

MIT — see [LICENSE](LICENSE).
