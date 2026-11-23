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
import sys
from pathlib import Path

ENC = "iso-8859-1"
REPO = Path(__file__).resolve().parent.parent
# The raw original tree lives OUTSIDE the repo (it holds credentials and visitor data and must never be committed).
RAW_DEFAULT = Path(os.environ.get("OJ98_RAW") or (REPO.parent / "OrangeJuice98-private" / "site-raw"))
MASK = "????"   # placeholder for every redacted value
META_CHARSET = '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">'
HOSTS_SELF = r"(?:ojuice\.citeweb\.net|www\.citeweb\.net/ojuice)"

# ---- removal-request footer: added to every page that lists or describes people
REMOVAL_MARK = 'id="removal-note"'
REMOVAL_NOTE = (
    '<div id="removal-note" style="clear:both;margin:28px auto 8px;padding:6px 8px 0;width:60%;'
    'border-top:1px solid #808080;text-align:center;font:10px/14px Verdana,Arial,Helvetica,sans-serif;'
    'color:#808080;background:transparent">To be removed from this page, '
    '<a href="mailto:chandra_oj@pm.me" style="color:#808080;text-decoration:underline">contact me</a>.</div>'
)
_BODY_END = re.compile(r"(?is)</body\s*>(?!.*</body\s*>)")


def add_removal_note(text):
    if REMOVAL_MARK in text:
        return text
    m = _BODY_END.search(text)
    if m:
        return text[: m.start()] + REMOVAL_NOTE + "\n" + text[m.start():]
    return text + "\n" + REMOVAL_NOTE + "\n"

PEOPLE_PAGES = re.compile(r"^(?:handles/[^/]+\.htm|groups/[^/]+\.htm|birthdates\.html"
                          r"|[ef]/(?:credits|interview_[^/]+|photo\d*)\.htm)$")


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
<META name="robots" content="noindex,follow">
<TITLE>Orange Juice - %(title)s (archived)</TITLE>
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


def notice_page(title, body):
    return NOTICE % {"meta": META_CHARSET, "title": title, "body": body}


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
    made.append(("parties_come.html", notice_page("Who goes to which party?", body)))
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


ICQ_IMG_RE = re.compile(r"<img\b[^>]*online\.mirabilis\.com[^>]*>", re.I)   # ICQ presence badge (dead host, leaks UIN lookups)
MAILTO_A_RE = re.compile(r"<a\b[^>]*href\s*=\s*[\"']?mailto:[^>]*>(.*?)</a\s*>", re.I | re.S)
EMAIL_RE = re.compile(r"([A-Za-z0-9._+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")


def redact_emails(text):
    """mailto: links become plain text; `local@domain` becomes `local@????` (layout kept, domain masked)."""
    text = MAILTO_A_RE.sub(lambda m: m.group(1), text)
    text = re.sub(r"(?i)mailto:", "", text)   # any leftover mailto: (e.g. in a tag we did not match)
    return EMAIL_RE.sub(lambda m: m.group(1) + "@" + MASK, text)


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
    text = ICQ_IMG_RE.sub("", text)
    if rel in STATS_PAGES:
        text = redact_stats(text, ctx)
    if not ctx.opts.keep_emails:
        text = redact_emails(text)
    if not ctx.opts.birthdates_year:
        text = mask_birth_year(text)

    def attr(m):
        val = m.group(3) if m.group(3) is not None else m.group(4)
        q = m.group(2) or ""
        return "%s=%s%s%s" % (m.group(1), q, rewrite_url(val, ctx), q)
    text = ATTR_RE.sub(attr, text)
    text = LOCATION_RE.sub(lambda m: m.group(1) + m.group(2) + rewrite_url(m.group(3), ctx) + m.group(2), text)
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


# ------------------------------------------------------------------------- main
def run(ctx):
    site, out = ctx.site, ctx.out
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
            if not ctx.opts.keep_emails and low.endswith(".txt") and not looks_utf8(full.read_bytes()):
                write_text(dst, redact_emails(read_text(full)))
            else:
                shutil.copy2(full, dst)
            ctx.copied += 1

    for new_rel, (enc, text) in outputs.items():
        if is_people_page(new_rel):
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

    meta = Path(opts.meta_dir)
    meta.mkdir(parents=True, exist_ok=True)
    seen, lines = set(), []
    for old, new in ctx.redirects:
        if old not in seen:
            seen.add(old)
            lines.append("%s\t%s" % (old, new))
    (meta / "redirects.tsv").write_text("\n".join(lines) + "\n", encoding="ascii", errors="replace")
    (meta / "excluded.txt").write_text("\n".join(ctx.excluded) + "\n", encoding="utf-8")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
