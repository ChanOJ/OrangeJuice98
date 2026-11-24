#!/usr/bin/env python3
"""Verify the statified build (stdlib only, read-only).

    python3 scripts/verify_build.py [--build site] [--raw PATH]

Hard checks (exit 1 on failure):
  * no ASP/SSI leftovers (`<%`, `%>`, `#include`, `$$...$$` macros, VBScript/FileSystemObject calls)
  * no `<form` anywhere; no *.shtml / *.inc / *.asp file or `.shtml` link target
  * nothing matching the exclusion globs was published
  * every html page declares its charset (iso-8859-1 unless it really is UTF-8)
  * no NEW broken internal link compared with the original site
Reported (not failing): broken-link counts original vs build (exact and case-only), IPv4-like strings.

Link resolution: href/src/background/action/<frame src>; external schemes ignored;
query/fragment dropped; a directory resolves to index.html / index.htm / index.shtml.
In the ORIGINAL tree `parties_come.shtml?party=X` counts as valid when data/X.txt exists
(that is what the ASP did).
"""
import argparse
import fnmatch
import os
import re
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
LINK_RE = re.compile(r"""(?ix)\b(?:href|src|background|action)\s*=\s*(?:(["'])(.*?)\1|([^\s"'>]+))""")
SKIP_SCHEME = re.compile(r"(?i)^(?:[a-z][a-z0-9+.-]*:|//|#|\$\$|<%)")
LEFTOVERS = [
    ("asp open tag", re.compile(r"<%")),
    ("asp close tag", re.compile(r"%>")),
    ("ssi include", re.compile(r"<!--\s*#(?:include|exec|echo|config)", re.I)),
    ("host macro $$..$$", re.compile(r"\$\$[A-Z]+(?::[^$]*)?\$\$")),
    ("vbscript api", re.compile(r"(?i)\b(?:FileSystemObject|OpenTextFile|CreateTextFile|Request\.(?:Form|QueryString|ServerVariables)|Server\.MapPath|Response\.(?:Write|Redirect))\b")),
    ("form tag", re.compile(r"(?i)<form\b")),
    ("'logins' (credentials file)", re.compile(r"(?i)logins")),
    ("e-mail address", re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")),
    ("mailto:", re.compile(r"(?i)mailto:")),
    ("birth year in handle sheet", re.compile(r"(?is)birthdate\s*<font[^>]*>\(dd/mm/yyyy\)</font>:\s*<br>\s*<font[^>]*>\s*\d{1,2}/\d{1,2}/\d{2,4}\s*<")),
]
MAILTO_ADDR = re.compile(r"(?i)mailto:([^\"'>\s?]*)")
EMAIL_ADDR = re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Third-party text files (demo results, .nfo) are published byte-identical: only the site's own data is scanned.
OWN_TXT = ("news/", "data/")
# The removal-request footer (statify.py) carries the only mailto: of the site: leave it out of the leftovers scan.
REMOVAL_NOTE_RE = re.compile(r'(?s)<div id="removal-note".*?</div>')
IPV4 = re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")


def rd(p):
    return Path(p).read_bytes().decode(ENC)


def all_files(root):
    out = []
    for r, ds, fs in os.walk(root):
        ds.sort()
        for f in fs:
            out.append(Path(r, f).relative_to(root).as_posix())
    return sorted(out)


def load_globs(path):
    return [l.strip() for l in rd(path).splitlines() if l.strip() and not l.startswith("#")] if path.is_file() else []


def excluded(rel, globs):
    base = rel.rsplit("/", 1)[-1]
    for g in globs:
        g = g.lstrip("/")
        if g.endswith("/"):
            d = g.rstrip("/")
            if rel == d or rel.startswith(d + "/"):
                return True
        elif "/" not in g:
            if fnmatch.fnmatchcase(base, g):
                return True
        elif fnmatch.fnmatchcase(rel, g) or (g.startswith("**/") and fnmatch.fnmatchcase(rel, g[3:])):
            return True
    return False


def extract_links(text):
    for m in LINK_RE.finditer(text):
        v = m.group(2) if m.group(2) is not None else m.group(3)
        if v is not None:
            yield v.strip()


def norm(p):
    return re.sub(r"(?i)\.shtml$", ".html", p)


def check_links(root, files_set, lower_map, site_data=None, original=False):
    """Returns {(source rel, resolved target): 'missing' | 'case'} for internal links that do not resolve.
    `.shtml` names are normalised to `.html` so original and build can be compared."""
    bad = {}
    for rel in files_set:
        low = rel.lower()
        if not low.endswith((".htm", ".html", ".shtml")):
            continue
        text = rd(root / rel)
        for raw in extract_links(text):
            if not raw or SKIP_SCHEME.match(raw) or "<%" in raw or re.search(r"[()\\;+]", raw):
                continue
            u = raw.split("#", 1)[0]
            path, _, query = u.partition("?")
            if path == "":
                continue
            if original and path.lower().endswith("parties_come.shtml") and query.lower().startswith("party="):
                party = query[6:].split("&")[0]
                if site_data and ("data/%s.txt" % party) in files_set:
                    continue
                path = path[:-len("parties_come.shtml")] + "parties_come_%s.html" % party
            path = unquote(path)
            if path.startswith("/"):
                target = os.path.normpath(path.lstrip("/"))
            else:
                target = os.path.normpath(os.path.join(os.path.dirname(rel), path))
            if target.startswith(".."):
                bad[(norm(rel), norm(target))] = "missing"
                continue
            target = target.replace(os.sep, "/")
            if target == ".":
                target = ""
            cands = [target]
            if target == "" or path.endswith("/") or ("." not in target.rsplit("/", 1)[-1]):
                base = target.rstrip("/")
                pre = base + "/" if base else ""
                cands = [pre + n for n in ("index.html", "index.htm", "index.shtml", "default.htm")]
                if not path.endswith("/") and target in files_set:
                    cands.append(target)
            if any(c in files_set for c in cands):
                continue
            kind = "case" if any(c.lower() in lower_map for c in cands) else "missing"
            bad[(norm(rel), norm(target))] = kind
    return bad


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", "--site", dest="site", default=os.environ.get("OJ98_RAW") or str(REPO.parent / "OrangeJuice98-private" / "site-raw"),
                    help="raw original tree, for the link comparison only (optional; skipped when absent, e.g. in CI)")
    ap.add_argument("--build", default=str(REPO / "site"), help="published tree to check (default: <repo>/site)")
    ap.add_argument("--exclude-file", action="append", default=None)
    ap.add_argument("--show", type=int, default=15, help="how many offending items to print per check")
    a = ap.parse_args(argv)
    site, build = Path(a.site), Path(a.build)
    if not build.is_dir():
        print("build folder missing: %s (run scripts/statify.py first)" % build)
        return 2
    failures = 0

    def fail(title, items):
        nonlocal failures
        failures += 1
        print("FAIL %s: %d" % (title, len(items)))
        for it in items[:a.show]:
            print("   ", it)
        if len(items) > a.show:
            print("    ... +%d more" % (len(items) - a.show))

    bfiles = all_files(build)
    bset = set(bfiles)
    missing = [f for f in ("index.html", "birthdates.html", "e/news.html", "f/news.html") if f not in bset]
    if missing or len(bfiles) < 1000:
        fail("build looks incomplete (missing key pages / too few files)", missing or [str(len(bfiles))])
    print("build: %d files" % len(bfiles))

    # 1. leftovers (text files only: html + txt)
    # addresses published whole on purpose (nick, role, first name): build-meta/kept-emails.txt, written by statify.py
    kf = REPO / "build-meta" / "kept-emails.txt"
    kept = {l.strip().lower() for l in rd(kf).splitlines() if l.strip()} if kf.is_file() else set()
    hits = defaultdict(list)
    for rel in bfiles:
        low = rel.lower()
        if low.endswith((".htm", ".html")) or (low.endswith(".txt") and rel.startswith(OWN_TXT)):
            t = REMOVAL_NOTE_RE.sub("", rd(build / rel))
            for label, rx in LEFTOVERS:
                if label == "form tag" and not low.endswith((".htm", ".html")):
                    continue
                if label == "e-mail address":
                    if any(m.group(0).lower() not in kept for m in EMAIL_ADDR.finditer(t)):
                        hits[label].append(rel)
                elif label == "mailto:":
                    if any(m.group(1).lower() not in kept for m in MAILTO_ADDR.finditer(t)):
                        hits[label].append(rel)
                elif rx.search(t):
                    hits[label].append(rel)
    for label, _ in LEFTOVERS:
        if hits[label]:
            fail("leftover '%s' in" % label, hits[label])
    shtml = []
    for rel in bfiles:
        if rel.lower().endswith((".htm", ".html")):
            for raw in extract_links(rd(build / rel)):
                if not SKIP_SCHEME.match(raw) and raw.split("#")[0].split("?")[0].lower().endswith(".shtml"):
                    shtml.append("%s -> %s" % (rel, raw))
    if shtml:
        fail("internal links still pointing to .shtml", shtml)
    bd = rd(build / "birthdates.html") if "birthdates.html" in bset else ""
    years = re.findall(r"\[\d\d/\d\d/(?:19)?\d\d\]", bd)
    if years:
        fail("birth year in birthdates.html", years)
    # 2. forbidden file types
    bad = [r for r in bfiles if r.lower().endswith((".shtml", ".inc", ".asp", ".aspx", ".cgi", ".dll", ".exe", ".bat"))]
    if bad:
        fail("server-side/executable files present", bad)
    # 3. exclusion globs honoured
    globs = []
    for f in (a.exclude_file or [REPO / "scripts" / "exclude-globs.txt", REPO / "docs" / "exclude-globs.txt"]):
        globs += load_globs(Path(f))
    pub = [r for r in bfiles if excluded(r, globs)]
    if pub:
        fail("published files matching exclusion globs", pub)
    # 4. charset declaration
    nocs = []
    for rel in bfiles:
        if rel.lower().endswith((".htm", ".html")) and not re.search(rb"(?i)charset\s*=", (build / rel).read_bytes()):
            nocs.append(rel)
    if nocs:
        fail("html without charset declaration", nocs)

    # 5. links: build alone, then compared with the raw original when it is available
    b_bad = check_links(build, bset, {f.lower() for f in bset})
    b_missing = {k for k, v in b_bad.items() if v == "missing"}
    b_case = {k for k, v in b_bad.items() if v == "case"}
    print("broken internal links  build:    %d missing + %d case-only (%d pages)"
          % (len(b_missing), len(b_case), len({k[0] for k in b_bad})))
    if site.is_dir():
        sfiles = all_files(site)
        sset = set(sfiles)
        s_bad = check_links(site, sset, {f.lower() for f in sset}, site_data=True, original=True)
        print("broken internal links  original: %d missing + %d case-only (%d pages)"
              % (sum(v == "missing" for v in s_bad.values()), sum(v == "case" for v in s_bad.values()),
                 len({k[0] for k in s_bad})))
        new = sorted(k for k in b_missing if k not in s_bad)
        if new:
            fail("NEW broken links (not broken in original)", ["%s -> %s" % k for k in new])
        else:
            print("ok: no new broken link vs original")
    else:
        print("notice: raw tree not found (%s): comparison with the original skipped (CI mode)" % site)
    by_target = defaultdict(int)
    for (_r, raw) in b_missing:
        by_target[raw] += 1
    if b_missing:
        top = sorted(by_target.items(), key=lambda t: -t[1])[:a.show]
        print("most frequent remaining broken targets:", ", ".join("%s (x%d)" % t for t in top))
    if b_case:
        print("case-only mismatches (work on Windows, 404 on bunny): e.g.",
              ", ".join("%s -> %s" % k for k in sorted(b_case)[:5]))

    # 6. IPv4: allowed only as the host of an http(s) URL (1990s servers of groups/links, no visitor data)
    ips, urlips = [], 0
    for rel in bfiles:
        if rel.lower().endswith((".htm", ".html", ".txt")):
            t = rd(build / rel)
            for m in IPV4.finditer(t):
                if t[max(0, m.start() - 8):m.start()].lower().endswith("://"):
                    urlips += 1
                else:
                    ips.append("%s: %s" % (rel, t[max(0, m.start() - 25):m.end() + 10].replace("\n", " ")))
    if ips:
        fail("IPv4 outside a URL host", ips)
    print("ok: %d IPv4 only as URL hosts (allow-listed), 0 elsewhere" % urlips if not ips else "")

    print("RESULT:", "FAIL" if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
