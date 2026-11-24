#!/usr/bin/env python3
"""Statify the 1998 Orange Juice classic-ASP site into plain static HTML.

    python3 scripts/statify.py [--raw PATH] [--out site] [options]

Reads ONLY from the source tree (never writes to it). Every page that used
`<% ... %>` (VBScript, served from `.shtml`) is rendered once from the current
flat-file data into a `.html` file; the other files are copied as-is.
See docs/data-model.md for the reverse-engineered data model and
docs/security-audit.md for what must not be published.

Python 3.9+, standard library only. Files are read/written as iso-8859-1
(lossless round trip), and every HTML page declares that charset.
"""
import argparse
import fnmatch
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
# The raw original tree lives OUTSIDE the repo (it holds credentials and visitor data and must never be committed).
RAW_DEFAULT = Path(os.environ.get("OJ98_RAW") or (REPO.parent / "OrangeJuice98-private" / "site-raw"))
MASK = "????"   # placeholder for every redacted value
META_CHARSET = '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
HOSTS_SELF = r"(?:ojuice\.citeweb\.net|www\.citeweb\.net/ojuice)"

# ---- removal-request footer: added to every page that shows personal data (family name, full postal
# address, or an e-mail whose local part is in clear). Pages with nicks only, role addresses or no
# personal data do not get it. Its width is the site's own column (630 px tables), centred.
REMOVAL_MARK = 'id="removal-note"'
REMOVAL_NOTE = (
    '<div id="removal-note" style="clear:both;margin:28px 0 8px 10px;padding:6px 8px 0;width:630px;max-width:96%;'
    'box-sizing:border-box;border-top:1px solid #808080;text-align:left;'
    'font:10px/14px Verdana,Arial,Helvetica,sans-serif;'
    'color:#808080;background:transparent">To be removed from this page, '
    '<a href="mailto:chandra_oj&#64;pm.me" style="color:#808080;text-decoration:underline">contact me</a>.</div>'
)
_BODY_END = re.compile(r"(?is)</body\s*>(?!.*</body\s*>)")


def add_removal_note(text):
    if REMOVAL_MARK in text:
        return text
    m = _BODY_END.search(text)
    if m:
        return text[: m.start()] + REMOVAL_NOTE + "\n" + text[m.start():]
    return text + "\n" + REMOVAL_NOTE + "\n"

# Pages that always carry the footer (real names in the text); every other page gets it from what its
# e-mail addresses show (see ``Privacy`` below), or from a third-party file it links (``Ctx.linkers``).
PEOPLE_PAGES = re.compile(r"^(?:birthdates\.html|[ef]/(?:credits|interview_[^/]+|photo\d*)\.htm)$")


def is_people_page(rel):
    return bool(PEOPLE_PAGES.match(rel))


MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
# VBScript MonthName(n, True) follows the server locale; the host was French.
# Assumption (not verifiable from the sources): English pages -> English, French pages -> French.
MONTHS_FR = ["janv.", "f\xe9vr.", "mars", "avr.", "mai", "juin", "juil.", "ao\xfbt", "sept.", "oct.", "nov.", "d\xe9c."]
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]


class Ctx:
    """Run-wide options and counters."""

    def __init__(self, site, out, opts):
        self.site = site
        self.out = out
        self.opts = opts
        self.globs = []
        self.excluded = []
        self.rendered = []      # (old rel, new rel)
        self.redirects = []     # (old url, new url)
        self.copied = 0
        self.warnings = []
        self.skipped_utf8 = []
        self.unlinked = {}
        self.stats_masked = 0
        self.site_files = None
        self.privacy = Privacy()
        self.linkers = {}       # page rel -> third-party files with personal data it links
        self.notes = []
        self.published = set()
        self.third_party_personal = set()

    def warn(self, msg):
        self.warnings.append(msg)


# --------------------------------------------------------------------------- io
def read_text(p):
    return Path(p).read_bytes().decode(ENC)


def write_text(p, s):
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(s.encode(ENC, errors="xmlcharrefreplace"))


def read_lines(p):
    t = read_text(p).replace("\r\n", "\n").replace("\r", "\n")
    ls = t.split("\n")
    if ls and ls[-1] == "":
        ls.pop()
    return ls


# ------------------------------------------------------------------- exclusions
def load_globs(path):
    out = []
    if path.is_file():
        for line in read_text(path).splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def is_excluded(rel, globs):
    """gitignore-like: no '/' -> matches a basename anywhere; trailing '/' -> a directory;
    otherwise fnmatch on the whole path relative to site/ ('*' crosses '/')."""
    base = rel.rsplit("/", 1)[-1]
    for g in globs:
        g = g.lstrip("/")
        if g.endswith("/"):
            d = g.rstrip("/")
            if rel == d or rel.startswith(d + "/") or ("/" not in d and ("/" + d + "/") in ("/" + rel)):
                return True
        elif "/" not in g:
            if fnmatch.fnmatchcase(base, g):
                return True
        elif fnmatch.fnmatchcase(rel, g):
            return True
    return False


# --------------------------------------------------------------- VBScript bits
def parse_vb_arrays(src):
    """Extract `Name = array(...)` literals (strings/numbers, `_` continuations)."""
    arrays = {}
    for m in re.finditer(r"(?m)^\s*(\w+)\s*=\s*array\(", src):
        i, vals = m.end(), []
        while i < len(src):
            c = src[i]
            if c == ")":
                break
            if c == '"':
                j, buf = i + 1, []
                while True:
                    if src[j] == '"':
                        if src[j + 1:j + 2] == '"':
                            buf.append('"')
                            j += 2
                            continue
                        break
                    buf.append(src[j])
                    j += 1
                vals.append("".join(buf))
                i = j + 1
            elif c.isdigit() or c == "-":
                j = i
                while j < len(src) and (src[j].isdigit() or src[j] == "-"):
                    j += 1
                vals.append(int(src[i:j]))
                i = j
            else:
                i += 1  # whitespace, commas, `_` line continuations
        arrays[m.group(1)] = vals
    return arrays


def html_encode(s):
    """Server.HTMLEncode."""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ------------------------------------------------------------------- templates
def is_fr(rel):
    return rel.startswith("f/")


def archived_inline(rel):
    msg = ("[ archiv\xe9 : ce formulaire n'est plus disponible &#8211; ce site est une copie en lecture seule ]"
           if is_fr(rel) else
           "[ archived: this form is no longer available &#8211; this site is a read-only snapshot ]")
    return '<P><FONT size="1" face="Verdana" color="#808080">%s</FONT></P>' % msg


NOTICE = """<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<HTML>
<HEAD>
%(meta)s
%(robots)s<TITLE>Orange Juice - %(title)s (archived)</TITLE>
</HEAD>
<BODY bgcolor="#000000" text="#FFFFFF" link="#FF8040" vlink="#FF8040" alink="#FF8040">
<CENTER>
<FONT size="2" face="Verdana">
<A HREF="/"><IMG src="/pictures/ojgl.gif" alt="Orange Juice" border="0" width="400" height="186"></A><BR><BR>
<FONT size="4" color="#FF8040"><B>%(title)s</B></FONT><BR><BR>
%(body)s<BR><BR>
<FONT color="#808080">This page used to be interactive. Orange Juice is now a read-only archive:
nothing can be added, sent or modified here.</FONT><BR><BR>
[ <A HREF="/e/news.html">&gt;NEWS</A> /
<A HREF="/birthdates.html">&gt;PEOPLE</A> /
<A HREF="/parties_incoming.htm">&gt;PARTIES</A> /
<A HREF="/e/misc.htm">&gt;MISC</A> /
<A HREF="/links.htm">&gt;LINKS</A> /
<A HREF="/e/faq.htm">&gt;FAQ</A> /
<A HREF="/e/credits.htm">&gt;CREDITS</A> ]
</FONT>
</CENTER>
</BODY>
</HTML>
"""


def notice_page(title, body, indexable=False):
    """Notice pages that replace a form stay out of the search results; the list of party lists is a real page."""
    robots = "" if indexable else '<META name="robots" content="noindex,follow">\n'
    return NOTICE % {"meta": META_CHARSET, "title": title, "body": body, "robots": robots}


# ---------------------------------------------------------------- page renderers
def load_logins(site):
    """news/logins.txt: 4 lines per writer (login, scrambled password, name, email). Password NOT kept."""
    ls = read_lines(site / "news" / "logins.txt")
    out, i = {}, 0
    while i + 3 < len(ls) + 1 and i < len(ls):
        login = ls[i]
        if login == "":
            break
        name = ls[i + 2] if i + 2 < len(ls) else ""
        email = ls[i + 3] if i + 3 < len(ls) else ""
        out[login] = (name, email)
        i += 4
    return out


def render_news(ctx, index_name, kind, lang):
    """disp_cur.shtml (first 9 entries, '[dd Mon yy]') / disp_old.shtml (rest, '[dd/mm/yy]')."""
    site = ctx.site
    logins = load_logins(site)
    idx = [x.strip() for x in read_lines(site / "news" / index_name)]
    idx = [x for x in idx if x]
    months = MONTHS_FR if lang == "f" else MONTHS_EN
    names = idx[:9] if kind == "cur" else idx[9:]
    parts = []
    for fn in names:
        p = site / "news" / fn
        if not p.is_file():
            ctx.warn("news: %s listed in %s but missing" % (fn, index_name))
            continue
        ls = read_lines(p)
        ls += [""] * (5 - len(ls))
        link, target, subject, news, login = ls[:5]
        yy, mm, dd = fn[2:4], fn[4:6], fn[6:8]
        if kind == "cur":
            date = "[%s %s %s]" % (dd, months[int(mm) - 1], yy)
        else:
            date = "[%s/%s/%s]" % (dd, mm, yy)
        name, email = logins.get(login, ("", ""))
        head = "<B>%s</B>" % subject
        if link:
            tgt = ' target="%s"' % target if target else ""
            head = '<A href="%s"%s>%s</A>' % (link, tgt, head)
        if email and not ctx.opts.strip_emails:
            who = ('<A HREF="mailto:%s?subject=%s" TITLE="Mail a question to the author of this news">'
                   '<FONT color="#FFFFFF"><I>%s</I></FONT></A>'
                   % (email, html_encode("Orange Juice News (About -> %s)" % subject), name))
        else:
            who = '<FONT color="#FFFFFF"><I>%s</I></FONT>' % name
        parts.append(
            '<P>\n  <FONT size="3" color="#FF8040">%s</FONT>\n'
            '  <FONT size="1" color="#FF8040">%s</FONT><BR>\n  %s<BR>\n'
            '  <FONT size="1">(%s)</FONT>\n</P>\n' % (head, date, news, who))
    return "\n".join(parts)


def render_news_page(ctx, text, rel):
    lang = "f" if is_fr(rel) else "e"
    pat = re.compile(r'<%\s*INDEXNAME\s*=\s*"([^"]+)"\s*%>\s*<!--\s*#include\s+file\s*=\s*"[^"]*disp_(cur|old)\.shtml"\s*-->', re.I)
    if not pat.search(text):
        raise RuntimeError("%s: INDEXNAME/include pair not found" % rel)
    text = pat.sub(lambda m: render_news(ctx, m.group(1), m.group(2).lower(), lang), text)
    return text


def parse_onliner(ctx):
    p = ctx.site / "data" / "#onliner.txt"
    if not p.is_file():
        return []
    ls = read_lines(p)
    return [ls[i:i + 5] for i in range(0, len(ls) - 4, 5)]  # date, user-agent, host, phrase, ip


def onliner_rows(recs, with_meta):
    rows = []
    for date, ua, host, phrase, _ip in recs:
        if with_meta:
            ua = ua[:ua.find(")") + 1] if ")" in ua else ua
            meta = "%s - %s - %s" % (html_encode(date), html_encode(ua), html_encode(host))
        else:  # original layout "[date - browser - host]": browser and host masked
            meta = "%s - %s - %s" % (html_encode(date), MASK, MASK)
        rows.append('<TR>\n<TD align="center"><FONT size="1" face="Verdana" color="#7F7F7F">[%s]</FONT></TD>\n</TR>\n'
                    '<TR>\n<TD align="center"><FONT size="2" face="Verdana">%s</FONT></TD>\n</TR>\n'
                    % (meta, html.escape(phrase, quote=False)))
    return "".join(rows)


def render_index(ctx, text, rel):
    # 1. rotating banner (Application("OJUICEBanner")): pick the first of TabBanners.
    arr = parse_vb_arrays(text).get("TabBanners")
    if not arr:
        raise RuntimeError("index: TabBanners not found")
    text = re.sub(r"<%\s*TabBanners\s*=.*?%>\s*", "", text, count=1, flags=re.S)
    text = text.replace("<%=TabBanners(Index+1)%>", arr[1]).replace("<%=TabBanners(Index)%>", arr[0])
    # 2. onliner: form + ASP block -> static list of the messages currently on disk
    s = text.index('<FORM action="index.shtml"')
    e = text.index("%>", text.index("<% IF ERR_MSG")) + 2
    recs = parse_onliner(ctx)
    block = "<TABLE>\n%s</TABLE>\n%s\n" % (onliner_rows(recs, ctx.opts.onliner_meta), archived_inline(rel))
    return text[:s] + block + text[e:]


def render_logins(ctx, text, rel):
    rows = []
    for _login, (name, email) in load_logins(ctx.site).items():
        if email and not ctx.opts.strip_emails:
            rows.append('<A href="mailto:%s">%s</A><BR><BR>\n' % (email, name))
        else:
            rows.append("%s<BR><BR>\n" % name)
    return re.sub(r"<%.*?\bLOOP\s*%>", lambda m: "".join(rows), text, count=1, flags=re.S)


def render_birthdates(ctx, text, rel):
    arr = parse_vb_arrays(text)
    names, links, days, months, years = (arr[k] for k in ("TabNames", "TabLinks", "TabJours", "TabMois", "TabYears"))
    n = len(names)
    if not (len(links) == len(days) == len(months) == len(years) == n):
        raise RuntimeError("birthdates: arrays of different lengths")
    m = re.search(r'MAJDate\s*=\s*"([^"]*)"', text)
    majdate = m.group(1) if m else ""
    text = re.sub(r"<%\s*MAJDate.*?%>\s*", "", text, count=1, flags=re.S)  # arrays + write_width_()
    text = text.replace("<%=MAJDate%>", majdate)
    s = text.index('<P><FONT color="#FF8040" size="4"><B>Today</B>')
    e = text.index("</TD>", text.index("Next days"))
    keep_year = ctx.opts.birthdates_year
    out = ['<P><FONT color="#FF8040" size="4"><B>All birthdays</B></FONT>\n'
           '<FONT size="1">(%d handles, sorted by day of the year; the old &quot;today / next days&quot; '
           'box needed a server clock)</FONT></P>\n' % n]
    nolink = []

    def person(i):  # link only to sheets that exist (newer handles may have no exported sheet)
        if (ctx.site / "handles" / (links[i] + ".htm")).is_file():
            return '<A href="handles/%s.htm">%s</A>' % (links[i], names[i])
        nolink.append(links[i])
        return names[i]
    cur, bad = None, []
    for i in range(n):
        if not (1 <= months[i] <= 12 and 1 <= days[i] <= 31):
            bad.append(i)   # damaged entry in the source arrays (month 0 or garbage): date unknown
            continue
        if months[i] != cur:
            if cur is not None:
                out.append("</P>\n")
            cur = months[i]
            out.append('<P><FONT color="#FF8040"><B>%s</B></FONT><BR>\n' % MONTHS_LONG[cur - 1])
        date = "%02d/%02d/" % (days[i], months[i]) + ("%d" % (years[i] + 1900) if keep_year else MASK)
        out.append('[%s] %s<BR>\n' % (date, person(i)))
    if bad:
        if cur is not None:
            out.append("</P>\n")
        out.append('<P><FONT color="#FF8040"><B>Date unknown</B></FONT> '
                   '<FONT size="1">(birth month damaged in the original data)</FONT><BR>\n')
        for i in bad:
            out.append('[??/??] %s<BR>\n' % person(i))
        ctx.warn("birthdates: %d entries with an invalid month listed under 'Date unknown'" % len(bad))
    out.append("</P>\n    ")
    if nolink:
        ctx.warn("birthdates: %d handles without a sheet in handles/ (listed without link)" % len(nolink))
    return text[:s] + "".join(out) + text[e:]


PARTY_PAGE = """<!DOCTYPE HTML PUBLIC "-//IETF//DTD HTML//EN">
<HTML>
<HEAD>
%(meta)s
<TITLE>Orange Juice (http://ojuice.citeweb.net) - Who go to %(name)s</TITLE>
<META name="DESCRIPTION" content="Here are the people who go to the %(name)s">
<SCRIPT LANGUAGE="JavaScript"><!--
if(top==self)
{
  var URL = unescape(window.location.pathname);
  location.href = (confirm("OK for English version of Orange Juice,\\nCANCEL for the French one")?"e":"f") + "/frames.htm?" + URL;
};
// --></SCRIPT>
</HEAD>

<BODY bgcolor="#000000" text="#FFFFFF" link="#FF8040" vlink="#FF8040" alink="#FF8040">

<TABLE border="0" cellpadding="0" cellspacing="0" width="630">
  <TR>
    <TD colspan="4">&nbsp;</TD>
  </TR>
  <TR>
    <TD width="10">&nbsp;</TD>
    <TD valign="top" width="400"><FONT color="#FFFFFF" size="2" face="Verdana">
      <FONT color="#FF8040" size="6"><B>%(name)s</B></FONT>
      <HR size="1">
      <P>
%(people)s
      </P>
    </FONT></TD>
    <TD width="40">&nbsp;</TD>
    <TD valign="top" width="180"><FONT size="2" face="Verdana"><BR>
      <BR>
      <BR>
      <BR>
      <FONT color="#808080">[ archived: registration is closed &#8211; this site is a read-only snapshot ]</FONT>
    </FONT>
    </TD>
  </TR>
</TABLE>

<TABLE border="0" cellpadding="0" cellspacing="0" width="630">
  <TR>
    <TD width="10">&nbsp;</TD>
    <TD width="620" colspan="3"><FONT size="1" face="Verdana"><BR>
      [ <A HREF="e/news.shtml">&gt;NEWS</A> /
      <A HREF="birthdates.shtml">&gt;PEOPLE</A> /
      <A HREF="parties_incoming.htm">&gt;PARTIES</A> /
      <A HREF="e/misc.htm">&gt;MISC</A> /
      <A HREF="links.htm">&gt;LINKS</A> /
      <A HREF="e/faq.htm">&gt;FAQ</A> /
      <A HREF="e/credits.htm">&gt;CREDITS</A> ]<BR>
      </FONT><HR size="1" align="left">
    </TD>
  </TR>
</TABLE>
</BODY>
</HTML>
"""


def party_files(site):
    """Every data/<name>.txt is a party list (the ASP opens data/<?party=>.txt). '#...' files are system files."""
    return sorted(p for p in (site / "data").glob("*.txt") if not p.name.startswith("#"))


def render_parties(ctx):
    """parties_come.shtml?party=<x> -> parties_come_<x>.html (one per data/<x>.txt) + parties_come.html (index)."""
    site, made, index = ctx.site, [], []
    for p in party_files(site):
        ls = read_lines(p)
        if not ls:
            continue
        key = p.stem
        name, people = ls[0], ls[1:]
        if is_excluded("data/" + p.name, ctx.globs):
            continue
        # raw lines hold "handle<BR>"; neutralise any other tag a visitor could have stored
        people = [re.sub(r"<(?!BR>)", "&lt;", x, flags=re.I) for x in people]
        body = PARTY_PAGE % {"meta": META_CHARSET, "name": name, "people": "\n".join(people)}
        made.append(("parties_come_%s.html" % key, body))
        index.append((name, "parties_come_%s.html" % key, len(people)))
        ctx.redirects.append(("/parties_come.shtml?party=%s" % key, "/parties_come_%s.html" % key))
    index.sort(key=lambda t: t[0].lower())
    lst = "".join('<A href="%s">%s</A> <FONT size="1" color="#808080">(%d)</FONT><BR>\n' % (u, n, c)
                  for n, u, c in index)
    body = ('<FONT size="3">Lists of people who announced they would go to a party (frozen):</FONT><BR><BR>\n'
            + lst)
    made.append(("parties_come.html", notice_page("Who goes to which party?", body, indexable=True)))
    return made


def render_to99(ctx, text, rel):
    d = ctx.site / "to99_live"
    entries = []
    for p in sorted(d.glob("[0-9][0-9][0-9][0-9]*.txt"), reverse=True):
        ls = read_lines(p)
        n = p.stem
        entries.append('<P><FONT color="#4080FF" size="1">[%s/%s/%s %s:%s] <FONT size=2><B>%s</B></FONT></FONT><BR>\n%s</P>\n'
                       % (n[6:8], n[4:6], n[:4], n[8:10], n[10:12], ls[0] if ls else "", "<BR>".join(ls[1:])))
    if not entries:
        entries = ['<P><FONT color="#808080">(The live-report entries were stored outside the site tree and '
                   'were not preserved.)</FONT></P>\n']
    return re.sub(r"<%\s*BASEPATH.*NEXT\s*%>", lambda m: "".join(entries), text, count=1, flags=re.S)


SPECIAL_NOTICES = {
    "to99_live/add.shtml": ("TakeOver 99 live report: add an entry",
                            "Entries to the live report could only be added by a password-protected form."),
    "news/adde.shtml": ("Add a news (English)", "News were added by the staff through a password-protected form."),
    "news/addf.shtml": ("Add a news (French)", "News were added by the staff through a password-protected form."),
    "news/stats.shtml": ("Visitor statistics",
                         "Statistics were computed from a private visit log (browsers, systems, countries) that "
                         "is deliberately not published."),
    "handles_/handle.shtml": ("Handle sheet",
                              'Handle sheets were generated from a database query. The pre-rendered sheets are '
                              'still available in the <A HREF="/handles/handle_a.htm">handles list</A>.'),
}
# ASP includes: only meaningful inside the page that includes them; not published.
INCLUDE_ONLY = {"news/disp_cur.shtml", "news/disp_old.shtml", "news/add_inc.shtml"}


def render_shtml(ctx, rel, text):
    """Returns list of (output rel, text) for one .shtml source."""
    new = rel[:-len(".shtml")] + ".html"
    if rel in INCLUDE_ONLY:
        return []
    if rel in SPECIAL_NOTICES:
        t, b = SPECIAL_NOTICES[rel]
        return [(new, notice_page(t, b))]
    if rel == "parties_come.shtml":
        return render_parties(ctx)
    if rel == "index.shtml":
        text = render_index(ctx, text, rel)
    elif rel == "birthdates.shtml":
        text = render_birthdates(ctx, text, rel)
    elif rel in ("e/news.shtml", "f/news.shtml", "e/newsold.shtml", "f/newsold.shtml"):
        text = render_news_page(ctx, text, rel)
    elif rel == "news/logins.shtml":
        text = render_logins(ctx, text, rel)
    elif rel == "to99_live/to99_live.shtml":
        text = render_to99(ctx, text, rel)
    elif "<%" in text:
        raise RuntimeError("%s: ASP page without a renderer" % rel)
    return [(new, text)]


# ------------------------------------------------------------ generic html pass
FORM_RE = re.compile(r"<form\b.*?</form\s*>", re.I | re.S)
# The original "if (top == self) { ...confirm() language prompt...; location.href = frames.htm?page }" snippet of
# 1,300 pages: dropped (its else branch, when present, stays as a plain block). scripts/frame-restore.js now puts
# a page opened on its own back in its frames, with no prompt.
TOP_REDIRECT_RE = re.compile(r"if\s*\(\s*top\s*==\s*self\s*\)\s*\{[^{}]*\}[ \t]*;?(?:\s*else\s*(?=\{))?")
RICOCHET_RE = re.compile(r"https?://(?:www\.)?citeweb\.net/ricochet\?\d*;?(https?://[^\"'<> ]+)", re.I)
ATTR_RE = re.compile(r"""(?ix)\b(href|src|background|action)\s*=\s*(?:(["'])(.*?)\2|([^\s"'>]+))""")
LOCATION_RE = re.compile(r"""(?i)(location\.href\s*=\s*)(["'])(.*?)\2""")
INC_RE = re.compile(r"\$\$INC:([^$]+)\$\$")


def site_has(ctx, path):
    """Does the (root-relative) path exist in the source tree, as file, folder with index, or party list?"""
    if ctx.site_files is None:
        ctx.site_files = {p.relative_to(ctx.site).as_posix() for p in ctx.site.rglob("*") if p.is_file()}
    t = path.split("#", 1)[0].split("?", 1)[0].lstrip("/")
    if t in ctx.site_files:
        return True
    d = t.rstrip("/")
    pre = d + "/" if d else ""
    return any((pre + n) in ctx.site_files for n in ("index.shtml", "index.htm", "index.html"))


def rewrite_url(u, ctx):
    m = re.match(r"(?i)^https?://%s(/.*)?$" % HOSTS_SELF, u)
    if m:
        own = m.group(1) or "/"
        if not site_has(ctx, own):
            return u                    # not part of the archive (e.g. a live webcam image): leave as is
        u = own                         # own (dead) host -> root-relative
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*:|^//", u):
        return u                        # external / mailto: / javascript:
    u2, sep, frag = u.partition("#")
    path, qsep, query = u2.partition("?")
    if path.lower().endswith(".shtml"):
        pm = re.match(r"(?i)party=([^&]+)", query)
        if path.lower().endswith("parties_come.shtml") and pm:
            path, query, qsep = path[:-len("parties_come.shtml")] + "parties_come_%s.html" % pm.group(1), "", ""
        else:
            path = path[:-len(".shtml")] + ".html"
    return path + qsep + query + (sep + frag if sep else "")


STATS_LIST_RE = re.compile(r"(<i>)(.*?)(</i>)", re.I | re.S)
STATS_PAGES = ("stats.htm", "stats2.htm")


def redact_stats(text, ctx=None):
    """stats.htm / stats2.htm: the table layout stays; the long list of visitor hosts/IPs (one per line,
    868 entries) is replaced line by line by ????. Short lists (browser names) are not identifying."""
    def block(m):
        parts = re.split(r"(?i)(<br>)", m.group(2))
        if sum(1 for x in parts if x.lower() == "<br>") <= 100:
            return m.group(0)
        out = [(MASK if (x.strip() and x.lower() != "<br>") else x) for x in parts]
        if ctx is not None:
            ctx.stats_masked += sum(1 for x in parts if x.strip() and x.lower() != "<br>")
        return m.group(1) + "".join(out) + m.group(3)
    text = STATS_LIST_RE.sub(block, text)
    return re.sub(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", MASK, text)


# ICQ presence badge (`online.mirabilis.com/scripts/online.dll?icq=<uin>`, a live image from a dead host): replaced
# by the site's own red (offline) flower (pictures/icqoff.gif, the red "offline" flower of the legend), hard-coded, so no request
# goes to any ICQ server. The UIN, its pager link and the "ICQ state :" label stay as in the original.
ICQ_IMG_RE = re.compile(r"<img\b[^>]*online\.mirabilis\.com/[^>]*icq=(\d+)[^>]*>", re.I)
ICQ_FLOWER = '<IMG src="/pictures/icqoff.gif" align="top" width="18" height="18" border="0" alt="ICQ #%s" title="ICQ #%s">'
MAILTO_A_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']?mailto:([^\"'>\s?]*)[^>]*>(.*?)</a\s*>", re.I | re.S)
EMAIL_RE = re.compile(r"([A-Za-z0-9._+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")

# ---- e-mail policy (standing decision, updated 2026-11-24): the domain is masked (`local@????`) only when
# the part left of the `@` holds a family name. A nick (the handle of the page, or a role address) and a
# first name alone keep the full address. Anything else (family names, initials + name, unknown words) is
# masked: that is the safe default, and the doubtful ones are listed for the owner in `review-emails.txt`
# (outside the repository). Exception (owner decision 2026-11-24): on the people pages (handles/, groups/) there
# are too many to sort by hand, so every address there keeps its domain and shows only the first and last
# letter of the local part (`l******t@domain.com`).
ROLE_LOCALS = {"ojuice", "orangejuice", "info", "infos", "contact", "webmaster", "tech", "admin", "support",
               "sales", "office", "jobs", "mail", "postmaster", "press", "staff", "news", "request",
               "orangejuice-request", "hostmaster", "abuse", "help", "organisation", "organizers"}
FIRST_NAMES = set("""
aaron adam adrian adrien alain alan albert alejandro alex alexander alexandre alexandra alexis alfred ali alice
amy ana andre andrea andreas andrew andy angel angela anna anne anthony antoine anton antonio arnaud arne arthur
axel barbara bastien ben benjamin benoit bernard bernd bert bertrand bill bjorn bob boris brian bruno carl carlos
carole carsten catherine cedric celine charles charlotte chris christian christina christophe christopher claire
claude clement cyril damien dan daniel danny dave david denis didier dirk dominique eddie edouard edward elena
elise emile emilie emmanuel eric erik ernst esteban etienne eva fabian fabien fabrice felix fernando filip florent
florian francesco francis francois franck frank franz fred frederic frederik gabriel gary gaston gaetan geoffrey
george georges gerald gerard gilles giovanni greg gregory guillaume gunnar gustav hans harald helen helene henri
henrik henry herve hugo hugues igor ivan jack jacob jacques jakob james jan jason jean jeanluc jeanmarc jeanne
jeff jennifer jens jeremie jeremy jerome jesper jim joachim joakim joe joel john johan johannes jon jonas jonathan
jose josef joseph juan jules julia julian julien jurgen justin karl karsten kati katrin ken kevin kim klaus kurt
lars laura laurence laurent leo leon linda lionel lisa lucas lucie ludovic luc luca luis lukas magnus manuel marc
marcel marco marcus margaux maria marie marc marion mario marius mark markus marta martin mathias mathieu matt
matthew matthias maurice max maxime michael michel michele mick mike miguel mikael mika mikko monica nathalie
nicholas nick nico nicolas niels nils noel norbert olaf olivier oscar otto pablo pascal patrice patrick paul
paula pedro per peter petra phil philip philippe pierre rafael ralf raphael raymond rene reinhard remi remy
renaud richard rob robert roberto robin rodolphe roger roland roman ronald ronny ruben rudolf sam samuel sandra
sandrine sara sarah sebastian sebastien serge sergio sergey simon sofia sophie stefan stefano stephane stephen
steve steven stig stuart sven sylvain sylvie tanja teddy thibault thierry thomas tim timothy tobias tom tommy
tony torsten ulrich ulf valentin valerie vera victor vincent walter werner wilhelm william willy wolfgang
xavier yann yannick yves zoe
""".split())


KEEP_FILE = REPO / "scripts" / "email-keep.txt"
KEEP_LOCALS = {l.strip().lower() for l in KEEP_FILE.read_text(encoding="utf-8").splitlines()
               if l.strip() and not l.startswith("#")} if KEEP_FILE.is_file() else set()


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _base(s):
    return re.sub(r"\d+$", "", _norm(s))


def email_kind(local, nicks, known=frozenset()):
    """'role' | 'nick' (a handle: the page's own, or any handle/group of the site) | 'first' (a first name) | 'masked'."""
    l = local.lower()
    if _base(l) in ROLE_LOCALS or l in ROLE_LOCALS or l in KEEP_LOCALS:
        return "role"
    b = _base(l)
    if len(b) >= 3 and b in known:
        return "nick"
    for n in nicks:
        nb = _base(n)
        if len(nb) >= 3 and (b == nb or (len(nb) >= 4 and (nb in _norm(l))) or (len(b) >= 4 and b in _norm(n))):
            return "nick"
    if b in FIRST_NAMES:
        return "first"
    return "masked"


NICK_FONT_RE = re.compile(r'<font\b[^>]*size\s*=\s*["\']?6["\']?[^>]*>\s*(?:<b>)?([^<]{1,40})', re.I)
TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")


def page_nicks(text):
    """The handles a page is about: its big (size 6) headings, and the end of its title (`... - Nick`)."""
    out = {html.unescape(m.group(1)).strip() for m in NICK_FONT_RE.finditer(text)}
    m = TITLE_RE.search(text)
    if m and " - " in m.group(1):
        out.add(html.unescape(m.group(1).rsplit(" - ", 1)[1]).strip())
    return {n for n in out if n}


class Privacy:
    """Run-wide record of what the e-mail policy did, to place footers and to write the review files."""

    def __init__(self):
        self.page_flag = {}      # rel -> True when the page shows personal data through an address
        self.kept = set()        # full addresses published (written to build-meta/kept-emails.txt)
        self.masked = {}         # local part -> 'pattern' | 'unknown' (review file, never committed)
        self.masked_pages = {}
        self.known_nicks = frozenset()   # base form of every handle and group name of the site


def classify_masked(local):
    """Rough guess for the review file: does a masked local part look like a family name?"""
    if re.search(r"[._-]", local) or re.match(r"^[a-z][a-z]{4,}$", local.lower()) is None:
        return "pattern"
    return "unknown"


def out_rel(rel):
    return rel[:-len(".shtml")] + ".html" if rel.endswith(".shtml") else rel


def redact_emails(text, ctx=None, rel=""):
    """Apply the e-mail policy: `mailto:` links and addresses with a family name (or doubtful) become plain
    `local@????`; nick, role and first-name addresses stay whole (with their mailto: link)."""
    nicks = page_nicks(text) if rel.startswith(("handles/", "groups/")) else set()
    priv = ctx.privacy if ctx is not None else Privacy()

    people = rel.startswith(("handles/", "groups/"))

    def star(local):
        return local[0] + "*" * (len(local) - 2) + local[-1] if len(local) > 2 else local[0] + "*" * (len(local) - 1)

    def decide(local, domain):
        """True: keep whole; False: `local@????`; None (people pages): `l****t@domain`."""
        kind = email_kind(local, nicks, priv.known_nicks)
        if kind in ("masked", "first") and rel:
            priv.page_flag[out_rel(rel)] = True
        if people:
            return None
        if kind == "masked":
            priv.masked.setdefault(local.lower(), classify_masked(local))
            priv.masked_pages.setdefault(local.lower(), set()).add(rel)
            return False
        priv.kept.add(("%s@%s" % (local, domain)).lower())
        return True

    def anchor(m):
        em = EMAIL_RE.fullmatch(m.group(1).strip())
        if em and decide(em.group(1), em.group(2)) is True:
            return m.group(0)
        return m.group(2)
    text = MAILTO_A_RE.sub(anchor, text)

    def addr(m):
        r = decide(m.group(1), m.group(2))
        if r is True:
            return m.group(0)
        return star(m.group(1)) + "@" + m.group(2) if r is None else m.group(1) + "@" + MASK
    text = EMAIL_RE.sub(addr, text)
    # mailto: left in front of an address that has just been masked (a tag the anchor regex did not match)
    return re.sub(r"(?i)mailto:(?=[^\"'>\s]*(?:@\?\?\?\?|\*+[^@\"'>\s]@))", "", text)


# Phone numbers (BBS lists, a job ad, an old news item): the last four digits are masked, the rest stays.
PHONE_RES = [
    re.compile(r"(?<![\w/.])\+\d{1,3}[- ]\(?\d{1,4}\)?(?:[-. ]\d+){1,5}"),    # +33-1-4177-9218, +31 79 3460269
    re.compile(r"(?<![\d.])0[1-9](?:[ .]\d\d){4}(?!\d)"),                       # 01 40 39 11 11, 06.11.38.56.93
]


def mask_phones(text):
    def fn(m):
        s, n = list(m.group(0)), 0
        for i in range(len(s) - 1, -1, -1):
            if s[i].isdigit():
                s[i] = "?"
                n += 1
                if n == 4:
                    break
        return "".join(s)
    for rx in PHONE_RES:
        text = rx.sub(fn, text)
    return text


# handle sheets: "> birthdate (dd/mm/yyyy):" followed by the value (the year is masked)
BIRTH_RE = re.compile(r"(?is)(birthdate\s*<font[^>]*>\(dd/mm/yyyy\)</font>:\s*<br>\s*<font[^>]*>)([^<]*)(</font>)")


def mask_birth_year(text):
    """Keep the label and position; the year of `dd/mm/yyyy` becomes ????. Empty `.../.../...` values stay."""
    def fn(m):
        parts = m.group(2).strip().split("/")
        v = "/".join(parts[:2] + [MASK]) if len(parts) == 3 and parts[2].strip(". ") else m.group(2)
        return m.group(1) + v + m.group(3)
    return BIRTH_RE.sub(fn, text)


ANCHOR_RE = re.compile(r"<a\b[^>]*?\bhref\s*=\s*(?:([\"'])(.*?)\1|([^\s\"'>]+))[^>]*>(.*?)</a\s*>", re.I | re.S)


def site_target(ctx, rel, url):
    """Path (relative to site/) an internal link points to, or None for external/dynamic/unresolvable links."""
    m = re.match(r"(?i)^https?://%s(/.*)?$" % HOSTS_SELF, url)
    if m:
        url = m.group(1) or "/"
    if re.match(r"(?i)^[a-z][a-z0-9+.-]*:|^//|^#|^\$\$", url):
        return None
    path = url.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return None
    t = os.path.normpath(path.lstrip("/") if path.startswith("/") else os.path.join(os.path.dirname(rel), path))
    t = t.replace(os.sep, "/")
    return None if t.startswith("..") else t


def unlink_excluded(ctx, text, rel):
    """<a href=X>label</a> where X is a page the exclusion globs remove -> plain label (no dead link)."""
    def fn(m):
        url = m.group(2) if m.group(2) is not None else m.group(3)
        t = site_target(ctx, rel, url)
        if t and is_excluded(t, ctx.globs) and (ctx.site / t).is_file():
            ctx.unlinked[t] = ctx.unlinked.get(t, 0) + 1
            return m.group(4)
        return m.group(0)
    return ANCHOR_RE.sub(fn, text)


FACE_VERDANA_RE = re.compile(r"""(?i)(\bface\s*=\s*)(["']?)(verdana\b[^"'>]*)(\2)""")
FONT_FALLBACK = ", DejaVu Sans, Geneva, sans-serif"


def add_font_fallback(text):
    """Verdana only exists on Windows, macOS and iOS: elsewhere face="verdana" falls back to a
    serif font. Append sans-serif fallbacks to the existing attribute (no CSS, same layout)."""
    def fn(m):
        value = m.group(3)
        if "sans-serif" in value.lower():
            return m.group(0)
        return m.group(1) + m.group(2) + value.rstrip() + FONT_FALLBACK + m.group(4)
    return FACE_VERDANA_RE.sub(fn, text)


# ---- link repair: internal links that 404 on the original (checked by verify_build.py)
ROOT_PAGES_LANG = {"chat.htm": "e", "metastream.htm": "e", "pc_megademo_project.htm": "e",
                   "stats.htm": "e", "stats2.htm": "e"}
ROOT_UP_RE = re.compile(r"^\.\./")
# (page, original href) -> new href; None = no target exists: the link keeps its label, the image goes
LINK_FIXES = {
    ("demos/takeover98/main.htm", "to98res.zip"): "to98res.txt",          # the results file is the .txt
    ("demos/volcanic5/3dgfx.htm", "wir98_3dgfx01_tmb.jpg"): "../wired98/wir98_3dgfx01_tmb.jpg",
    ("demos/volcanic5/3dgfx.htm", "wir98_3dgfx03_tmb.jpg"): "../wired98/wir98_3dgfx03_tmb.jpg",
    ("demos/volcanic5/3dgfx.htm", "wir98_3dgfx06_tmb.jpg"): "../wired98/wir98_3dgfx06_tmb.jpg",
    ("e/faq.htm", "../archive/wrar202.zip"): None,                          # WinRAR 2.02: never in the archive
    ("f/faq.htm", "../archive/wrar202.zip"): None,
    ("groups/extravaganja.htm", "../handles/mr__splif1.htm"): "../handles/splif1.htm",
    ("groups/melting_pot.htm", "../e/newjuice.htm"): "../e/news.html",
    ("handles/handle_k.htm", "../e/reaction_kheops.htm"): None,             # only the French interview exists
    ("handles/kheops2.htm", "../e/reaction_kheops.htm"): None,
    ("parties_past.htm", "parties_come_overseas99.html"): None,             # no list for that party
    ("parties_past.htm", "../../../ojuice/archive/ast97inv_eng.txt"): "archive/ast97inv_eng.txt",
    ("tp8.htm", "photo1.htm"): None, ("tp8.htm", "photo1.jpg"): None,
    ("tp8.htm", "photo2.htm"): None, ("tp8.htm", "photo2.jpg"): None,
}
LINK_TAG_RE = re.compile(r"""(?is)(<a\b[^>]*?\bhref\s*=\s*(["']?)([^"'>\s]+)\2[^>]*>)(.*?)</a\s*>""")
IMG_SRC_RE = re.compile(r"""(?is)<img\b[^>]*?\bsrc\s*=\s*(["']?)([^"'>\s]+)\1[^>]*>""")


def fix_links(ctx, text, rel):
    """Repair what is broken in the source: relative nav links of the root pages, wrong paths, files that
    never existed. Case-only mismatches are fixed later, once the list of published files is known."""
    rel = out_rel(rel)
    lang = ROOT_PAGES_LANG.get(rel)

    def new_href(u):
        if (rel, u) in LINK_FIXES:
            return LINK_FIXES[(rel, u)], True
        if rel.count("/") == 0 and ROOT_UP_RE.match(u):
            return "/" + u[3:], True                # `../x` from a root page points outside the site
        if lang and rel.count("/") == 0:
            if re.match(r"^(?:news\.html|misc\.htm|faq\.htm|credits\.htm)(?:[#?].*)?$", u):
                return "/%s/%s" % (lang, u), True
        return u, False

    def anchor(m):
        u = m.group(3)
        v, changed = new_href(u)
        if not changed:
            return m.group(0)
        if v is None:
            return m.group(4)
        return m.group(0).replace(m.group(1), m.group(1).replace(u, v, 1), 1)

    def img(m):
        u = m.group(2)
        v, changed = new_href(u)
        if not changed:
            return m.group(0)
        return "" if v is None else m.group(0).replace(u, v, 1)
    text = LINK_TAG_RE.sub(anchor, text)
    return IMG_SRC_RE.sub(img, text)


EXT_A_RE = re.compile(r"""(?is)<a\b(?:[^>"']|"[^"]*"|'[^']*')*>""")
EXT_HREF_RE = re.compile(r"""(?is)\bhref\s*=\s*(?:"\s*([^"]*)"|'\s*([^']*)'|([^\s>]+))""")
ATTR_DROP_RE = re.compile(r"""(?is)\s+(?:target|rel)\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""")


def external_new_window(text):
    """Every page lives in frames and many hosts refuse framing: external http/https/ftp links open in a new
    tab (an existing target, `_top` included, is replaced). mailto: and internal links are left alone."""
    def tag(m):
        t = m.group(0)
        h = EXT_HREF_RE.search(t)
        u = (h.group(1) or h.group(2) or h.group(3) or "") if h else ""
        if not re.match(r"(?i)^(?:https?|ftp)://", u.strip()):
            return t
        t = ATTR_DROP_RE.sub("", t)
        return t[:-1].rstrip("/").rstrip() + ' target="_blank" rel="noopener">'
    return EXT_A_RE.sub(tag, text)


TITLES = {   # pages of the original without a <title> (the tab showed the URL)
    "search.htm": "Orange Juice - Search (archived)", "chat_title.htm": "Orange Juice - Chat",
    "rc5.html": "Orange Juice - RC5", "reb.htm": "Orange Juice - reb",
    "archive/demopartymilia98.htm": "Demo Party MILIA 98 - call, themes and rules",
}
PARTY_STUB_RE = re.compile(r"^parties_come_([a-z0-9_-]+)\.htm$")


def add_title(text, rel):
    if re.search(r"(?i)<title\b", text):
        return text
    m = PARTY_STUB_RE.match(rel)
    title = TITLES.get(rel) or ("Orange Juice - Who go to %s" % m.group(1) if m else None)
    if not title:
        return text
    tag = "<TITLE>%s</TITLE>" % html.escape(title, quote=False)
    h = re.search(r"(?i)<head[^>]*>", text)
    if h:
        return text[:h.end()] + "\n" + tag + text[h.end():]
    return tag + "\n" + text


VIEW_IMG_RE = re.compile(r'document\.write\("<IMG src=\\""\+GetCookie\(\)\+"\\" BORDER=0>"\);')
VIEW_IMG_Q_RE = re.compile(r"""(document\.writeln\('<IMG src="' \+ location\.search\.substring\(1,128\) \+ '".*?</A>'\);)""", re.S)
NO_PICTURE = '<FONT size="1" color="#808080">[ no picture selected: open one from a gallery ]</FONT>'


def fix_view_img(text, rel):
    """view_img.htm opened without a picture drew a broken image (`src` empty or `\\`): show a note instead."""
    if rel.rsplit("/", 1)[-1] != "view_img.htm":
        return text
    text = VIEW_IMG_RE.sub('if (GetCookie()) { document.write("<IMG src=\\""+GetCookie()+"\\" BORDER=0>"); } '
                           'else { document.write(%r); }' % NO_PICTURE, text)
    text = VIEW_IMG_Q_RE.sub(lambda m: "if (location.search.length > 1) { %s } else { document.writeln(%r); }"
                             % (m.group(1), NO_PICTURE), text)
    return text


CHAT_FOCUS_RE = re.compile(r"[ \t]*document\.f\.pseudo\.focus\(\);[ \t]*\r?\n?")


NOINDEX_META_RE = re.compile(r'(?i)(<meta\s+name="robots"\s+content=")noindex(,follow">)')


def convert_html(ctx, text, rel):
    def inc(m):
        p = ctx.site / m.group(1).lstrip("/")
        return read_text(p).rstrip("\r\n") if p.is_file() else ""
    text = INC_RE.sub(inc, text)
    for tok in ("$$PUB$$", "$$CPT$$", "$$FDATE$$"):     # host macros: ad slot, hit counter, file date
        text = text.replace(tok, "")
    text = RICOCHET_RE.sub(lambda m: m.group(1), text)  # click-counting redirector (dead host) -> direct link
    text = FORM_RE.sub(lambda m: archived_inline(rel), text)
    if not ctx.opts.keep_excluded_links:
        text = unlink_excluded(ctx, text, rel)
    text = ICQ_IMG_RE.sub(lambda m: ICQ_FLOWER % (m.group(1), m.group(1)), text)
    if rel in STATS_PAGES:
        text = redact_stats(text, ctx)
    if not ctx.opts.keep_emails:
        text = redact_emails(text, ctx, rel)
    text = mask_phones(text)
    if not ctx.opts.birthdates_year:
        text = mask_birth_year(text)
    text = fix_view_img(text, rel)
    if out_rel(rel) == "chat.htm":
        text = CHAT_FOCUS_RE.sub("", text)              # the chat form is gone: the script line raised an error

    def attr(m):
        val = m.group(3) if m.group(3) is not None else m.group(4)
        q = m.group(2) or ""
        return "%s=%s%s%s" % (m.group(1), q, rewrite_url(val, ctx), q)
    text = ATTR_RE.sub(attr, text)
    text = LOCATION_RE.sub(lambda m: m.group(1) + m.group(2) + rewrite_url(m.group(3), ctx) + m.group(2), text)
    text = TOP_REDIRECT_RE.sub("", text)
    text = fix_links(ctx, text, rel)
    text = external_new_window(text)
    if out_rel(rel) == "birthdates.html":
        text = NOINDEX_META_RE.sub(r"\1index\2", text)       # the birthday list is meant to be found
    if out_rel(rel) == "f/hack.htm":
        text = HACK_BGSOUND_RE.sub(lambda m: HACK_PLAYER_FMT % midi_hash(), text)
        text = re.sub(r"(?i)(<body\b[^>]*>)", r'\1<div id="midi-host" style="position:relative;width:800px;max-width:100%">', text, count=1)   # 800 px page
        text = re.sub(r"(?i)</body>", "</div></body>", text, count=1)
    text = citeweb_crosslinks(text)
    if out_rel(rel) in ("f/ojmenu.htm", "e/ojmenu.htm"):
        text = menu_player(text)
    text = add_title(text, out_rel(rel))
    return add_font_fallback(text)


def declare_charset(text):
    if re.search(r"(?i)charset\s*=", text):
        return text
    m = re.search(r"(?i)<head[^>]*>", text) or re.search(r"(?i)<html[^>]*>", text)
    if m:
        return text[:m.end()] + "\n" + META_CHARSET + text[m.end():]
    return META_CHARSET + "\n" + text


def looks_utf8(raw):
    if not re.search(rb"[\x80-\xff]", raw):
        return False
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def fix_case(text, rel, published, lower):
    """Links that differ from the real file name only by case work on Windows and 404 on the CDN: use the real name."""
    def attr(m):
        val = m.group(3) if m.group(3) is not None else m.group(4)
        if re.match(r"(?i)^[a-z][a-z0-9+.-]*:|^//|^#|^\$\$", val):
            return m.group(0)
        path, sep, rest = re.split(r"([?#])", val, maxsplit=1) if re.search(r"[?#]", val) else (val, "", "")
        if not path:
            return m.group(0)
        t = os.path.normpath(path.lstrip("/") if path.startswith("/") else os.path.join(os.path.dirname(rel), path)).replace(os.sep, "/")
        if t.startswith("..") or t in published or t.lower() not in lower:
            return m.group(0)
        real, segs, rsegs = lower[t.lower()], path.split("/"), lower[t.lower()].split("/")
        for k in range(1, min(len(segs), len(rsegs)) + 1):
            if segs[-k].lower() == rsegs[-k].lower():
                segs[-k] = rsegs[-k]
        q = m.group(2) or ""
        return "%s=%s%s%s" % (m.group(1), q, "/".join(segs) + sep + rest, q)
    return ATTR_RE.sub(attr, text)


# ---- menu jukebox: the two dead Modplug <embed>s of ojmenu.htm become a small player (scripts/music/)
MUSIC_SRC = REPO / "scripts" / "music"
MENU_EMBED_RE = re.compile(r"<embed\b[^>]*\.mdz[^>]*>\s*", re.I)
MENU_PLAYER = (
    '<div id="oj-player" hidden style="width:130px;height:36px;background:#000;color:#FF8040;'
    'font:10px/12px Verdana,DejaVu Sans,Geneva,sans-serif;overflow:hidden">'
    '<div style="display:flex;align-items:center;gap:3px;height:20px">'
    '<button type="button" data-act="play" aria-label="Play" style="width:30px;height:18px;padding:0;'
    'background:#000;color:#FF8040;border:1px solid #FF8040;font:9px Verdana,sans-serif;cursor:pointer">&#9654;</button>'
    '<button type="button" data-act="next" aria-label="Next track" title="Next track" style="width:26px;height:18px;'
    'padding:0;background:#000;color:#FF8040;border:1px solid #FF8040;font:9px Verdana,sans-serif;cursor:pointer">&#9197;</button>'
    '<input type="range" data-act="vol" min="0" max="100" value="70" aria-label="Volume" title="Volume" '
    'style="width:60px;height:14px;margin:0;accent-color:#FF8040"></div>'
    '<div data-act="name" style="height:14px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis"></div></div>'
    '<noscript><a href="../archive/ojtheme.mdz">theme (.mdz)</a><br><a href="../archive/pressme.mdz">press me (.mdz)</a></noscript>\n'
)


HACK_BGSOUND_RE = re.compile(r"<bgsound\b[^>]*>", re.I)
HACK_PLAYER_FMT = '<script src="/music/midi-player.js?v=%s" data-midi="Techno78.mid"></script>'


def midi_hash():
    import hashlib
    return hashlib.sha1((MUSIC_SRC / "midi-player.js").read_bytes()).hexdigest()[:8]


CITEWEB_RE = re.compile(r"(?i)\b((?:href|src)\s*=\s*[\"']?)https?://www\.citeweb\.net(?=[/\"'\s>])")


def citeweb_crosslinks(text):
    """Links to the old Citeweb host point to its archive, same path (owner decision 2026-11-24)."""
    return CITEWEB_RE.sub(lambda m: m.group(1) + "https://citeweb97.sauthier.family", text)


def music_hash():
    import hashlib
    return hashlib.sha1((MUSIC_SRC / "oj-player.js").read_bytes()).hexdigest()[:8]


def menu_player(text):
    """Replace the two Modplug embeds by the player widget and load its script (module, content-hashed URL)."""
    state = {"n": 0}

    def fn(m):
        state["n"] += 1
        return MENU_PLAYER if state["n"] == 1 else ""
    text = MENU_EMBED_RE.sub(fn, text)
    tag = '<script type="module" src="/music/oj-player.js?v=%s"></script>\n' % music_hash()
    m = _BODY_END.search(text)
    return text[:m.start()] + tag + text[m.start():] if m else text + tag


def build_music(ctx):
    """site/music/: the player, chiptune3 + libopenmpt, and the two modules unzipped from the original .mdz
    (the .mdz stay served as they are, for download)."""
    import zipfile
    dst = ctx.out / "music"
    dst.mkdir(parents=True, exist_ok=True)
    for f in sorted(MUSIC_SRC.iterdir()):
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    for name in ("ojtheme", "pressme"):
        with zipfile.ZipFile(ctx.site / "archive" / (name + ".mdz")) as z:
            (dst / (name + ".xm")).write_bytes(z.read(name + ".xm"))


# ------------------------------------------------------------------------- main
def collect_nicks(ctx):
    """Every handle sheet and group page names its nick in a size-6 heading: those names are not family names."""
    names = set()
    for sub in ("handles", "groups"):
        for p in sorted((ctx.site / sub).glob("*.htm")):
            if is_excluded(sub + "/" + p.name, ctx.globs):
                continue
            names |= {_base(n) for n in page_nicks(read_text(p))}
    ctx.privacy.known_nicks = frozenset(n for n in names if len(n) >= 3)


def run(ctx):
    site, out = ctx.site, ctx.out
    collect_nicks(ctx)
    outputs = {}   # rel -> (encoding, text)
    for root, dirs, files in os.walk(site):
        dirs.sort()
        for fn in sorted(files):
            full = Path(root) / fn
            rel = full.relative_to(site).as_posix()
            if is_excluded(rel, ctx.globs):
                ctx.excluded.append(rel)
                continue
            low = fn.lower()
            if low.endswith(".inc"):
                continue  # ASP/SSI include fragments: inlined where used, never published
            if low.endswith(".shtml"):
                for new_rel, text in render_shtml(ctx, rel, read_text(full)):
                    if new_rel in outputs:
                        raise RuntimeError("output collision: " + new_rel)
                    outputs[new_rel] = (ENC, declare_charset(convert_html(ctx, text, rel)))
                    if not new_rel.startswith("parties_come"):
                        ctx.rendered.append((rel, new_rel))
                        ctx.redirects.append(("/" + rel, "/" + new_rel))
                continue
            if low.endswith((".htm", ".html")):
                raw = full.read_bytes()
                if looks_utf8(raw):
                    ctx.skipped_utf8.append(rel)
                    outputs[rel] = ("utf-8", convert_html(ctx, raw.decode("utf-8"), rel))
                else:
                    outputs[rel] = (ENC, declare_charset(convert_html(ctx, raw.decode(ENC), rel)))
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            own_data = rel.startswith(("news/", "data/")) and low.endswith(".txt")
            if not ctx.opts.keep_emails and own_data and not looks_utf8(full.read_bytes()):
                write_text(dst, redact_emails(read_text(full), ctx, rel))      # the site's own data files
            else:
                shutil.copy2(full, dst)     # third-party files (demo texts, zips, modules): byte-identical
                if low.endswith((".txt", ".nfo", ".diz")) and not own_data:
                    t = read_text(full)
                    if EMAIL_RE.search(t) or any(r.search(t) for r in PHONE_RES):
                        ctx.third_party_personal.add(rel)
            ctx.published.add(rel)
            ctx.copied += 1

    ctx.published |= set(outputs)
    lower = {p.lower(): p for p in ctx.published}
    third = {os.path.basename(p).lower() for p in ctx.third_party_personal}
    for new_rel, (enc, text) in outputs.items():
        text = fix_case(text, new_rel, ctx.published, lower)
        if (is_people_page(new_rel) or ctx.privacy.page_flag.get(new_rel)
                or any(re.search(r'(?i)href\s*=\s*["\']?[^"\'>\s]*/?%s["\'>\s]' % re.escape(b), text) for b in third)):
            text = add_removal_note(text)
        dst = out / new_rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(text.encode(enc, errors="xmlcharrefreplace"))

    if ctx.opts.shtml_stubs:
        for old, new in ctx.rendered:
            write_text(out / old, '<HTML><HEAD>%s<META http-equiv="refresh" content="0; url=/%s">'
                       '<LINK rel="canonical" href="/%s"></HEAD><BODY><A href="/%s">moved</A></BODY></HTML>\n'
                       % (META_CHARSET, new, new, new))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", "--raw", dest="site", default=str(RAW_DEFAULT),
                    help="raw original tree (default: $OJ98_RAW or ../OrangeJuice98-private/site-raw)")
    ap.add_argument("--out", default=str(REPO / "site"), help="published tree: committed and deployed (default: <repo>/site)")
    ap.add_argument("--no-clean", action="store_true", help="keep existing files in the output folder (default: it is emptied first)")
    ap.add_argument("--exclude-file", action="append", default=None,
                    help="exclusion globs file (default: scripts/exclude-globs.txt + docs/exclude-globs.txt if present)")
    ap.add_argument("--meta-dir", default=str(REPO / "build-meta"), help="where redirects.tsv is written")
    ap.add_argument("--strip-emails", action="store_true", help="drop news-authors' mailto links in the news pages")
    ap.add_argument("--keep-excluded-links", action="store_true",
                    help="keep <a> links to pages removed by the exclusion globs (default: replace them by their label)")
    ap.add_argument("--keep-emails", action="store_true",
                    help="do NOT redact e-mail addresses (default: mailto: links removed, '@domain' dropped in html/txt)")
    ap.add_argument("--onliner-meta", action="store_true", help="show user-agent and host of onliner messages (default: date only)")
    ap.add_argument("--birthdates-year", action="store_true",
                    help="keep birth years (default: masked in birthdates.html and in handles/ sheets)")
    ap.add_argument("--shtml-stubs", action="store_true", help="also emit meta-refresh stubs at the old *.shtml names")
    opts = ap.parse_args(argv)

    site, out = Path(opts.site).resolve(), Path(opts.out).resolve()
    if not site.is_dir():
        ap.error("site folder not found: %s" % site)
    if out == site or site in out.parents or out in site.parents:
        ap.error("--out must be a separate folder, outside the source tree")
    ctx = Ctx(site, out, opts)
    files = opts.exclude_file or [REPO / "scripts" / "exclude-globs.txt", REPO / "docs" / "exclude-globs.txt"]
    for f in files:
        g = load_globs(Path(f))
        if g:
            print("exclusions: %d globs from %s" % (len(g), f))
        ctx.globs += g
    if out == REPO or out in REPO.parents or (out / ".git").exists() or out in (Path.home(), Path("/")):
        ap.error("refusing to empty %s (not a dedicated output folder)" % out)
    if not opts.no_clean and out.exists():
        for _ in range(3):  # Finder may drop a .DS_Store while we delete
            shutil.rmtree(out, ignore_errors=True)
            if not out.exists():
                break
    out.mkdir(parents=True, exist_ok=True)

    run(ctx)
    build_music(ctx)

    meta = Path(opts.meta_dir)
    meta.mkdir(parents=True, exist_ok=True)
    seen, lines = set(), []
    for old, new in ctx.redirects:
        if old not in seen:
            seen.add(old)
            lines.append("%s\t%s" % (old, new))
    (meta / "redirects.tsv").write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
    (meta / "excluded.txt").write_text("\n".join(ctx.excluded) + "\n", encoding="utf-8")
    # addresses published whole (nick, role, first name): verify_build.py allows exactly these
    (meta / "kept-emails.txt").write_text("\n".join(sorted(ctx.privacy.kept)) + "\n", encoding="ascii", errors="replace")
    review = REPO.parent / "OrangeJuice98-private" / "review-emails.txt"   # masked local parts: never committed
    if review.parent.is_dir():
        rows = ["%s\t%s\t%d page(s)" % (k, v, len(ctx.privacy.masked_pages[k])) for k, v in sorted(ctx.privacy.masked.items())]
        review.write_text("# local part\tguess (pattern = looks like a family name, unknown = could be a nick)\n"
                          + "\n".join(rows) + "\n", encoding="utf-8")

    print("e-mail policy: %d addresses kept whole, %d local parts masked (%d look like nicks/unknown)"
          % (len(ctx.privacy.kept), len(ctx.privacy.masked), sum(1 for v in ctx.privacy.masked.values() if v == "unknown")))
    print("rendered %d ASP pages (+%d party pages); copied %d files; excluded %d; redirects: %d"
          % (len(ctx.rendered), sum(1 for p in party_files(site)), ctx.copied, len(ctx.excluded), len(lines)))
    if ctx.stats_masked:
        print("stats pages: %d visitor host/IP lines masked" % ctx.stats_masked)
    if ctx.unlinked:
        print("links to excluded pages removed (kept the label): %s"
              % ", ".join("%s x%d" % kv for kv in sorted(ctx.unlinked.items())))
    if ctx.skipped_utf8:
        print("kept as UTF-8 (no latin-1 meta): %s" % ", ".join(ctx.skipped_utf8))
    for w in ctx.warnings:
        print("WARNING:", w, file=sys.stderr)
    # Crawlers and frames: sitemap.xml, robots.txt and the frame-restore script tag (scripts/seo.py).
    subprocess.run([sys.executable, "-I", str(REPO / "scripts" / "seo.py"), "--site", str(out)], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
