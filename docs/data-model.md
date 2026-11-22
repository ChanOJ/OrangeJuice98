# Orange Juice 1998-99: reverse-engineered data model

The site was classic ASP (VBScript, `<% %>`) hosted on citeweb.net, with pages named `.shtml`.
There was **no SQL database in the repo** (one page queried an MS SQL DSN, see §6; its data is gone).
The "database" was flat `.txt` files read/written through `Scripting.FileSystemObject`, plus two
`Application` variables. Encoding of everything: iso-8859-1, CRLF line ends.

Written 2026-11-22 from the raw original tree (`../OrangeJuice98-private/site-raw/`, outside the repo; see "Regenerating"). Companion tools: `scripts/statify.py` (renders), `scripts/verify_build.py` (checks).
Security findings are in `docs/security-audit.md`; this file only describes structure.

## 1. Server-side pieces

| Mechanism | Where | What it did |
|---|---|---|
| ASP pages `<% %>` in `.shtml` | 19 files (§3) | render from flat files, handle POSTs |
| ASP include `<!-- #include file="../news/disp_cur.shtml" -->` | `e|f/news.shtml`, `e|f/newsold.shtml`, `news/adde|addf.shtml` | news rendering / add form shared by both languages |
| Host macros `$$INC:/path$$`, `$$PUB$$`, `$$CPT$$`, `$$FDATE$$` | ~1100 `.htm` + `f|e/index.shtml` | **citeweb.net platform, not ASP**: `$$INC:` pastes a file (`keywords.inc`, `keywords2.inc`, `demos/title.inc`), `$$PUB$$` = ad banner slot (1056 pages), `$$CPT$$` = hit counter (menus), `$$FDATE$$` = file modification date, French long form (`parties_incoming.htm`, JS) |
| `Application("OJUICEBanner")` | `index.shtml` | rotating banner index, in memory only (lost at app restart) |
| Reverse DNS `TCPIP.DNS`, `PipeMail.1` | `index.shtml`, `news/add_inc.shtml` | host name of the poster; e-mail notification via a named pipe |
| Click redirector `http://www.citeweb.net/ricochet?<n>;<url>` | ~100 outbound links | counted clicks (log `ctw/ricochet.log`), then redirected |
| `formmail.cgi/.dll`, `cgi/search.htm`, `/ou/index.htm` | forms in 35 `.htm` | mail relay / site search / "ou" search, all on the dead host |

No `Session`, no cookies, no SQL writes. All writes are `CreateTextFile` / `OpenTextFile(...,8)` (append).

## 2. Flat files

| File | Format | Written by | Read by |
|---|---|---|---|
| `news/e_index.txt`, `news/f_index.txt` | one news file name per line, **newest first** (new entry is prepended) | `news/add_inc.shtml` | `disp_cur`, `disp_old` |
| `news/<lang>_<yymmdd>-<hhmm[ss]>.txt` (254) | 5 lines: `link`, `target` (`_top`/empty), `subject`, `body` (raw HTML, newlines already turned into `<BR>`), `login` | `add_inc.shtml` | `disp_cur`, `disp_old` |
| `news/logins.txt` | 4 lines per writer: `login`, password (rotated alphanumerics, see audit), `display name`, `e-mail`; ends at the first empty login | by hand | news pages, `logins.shtml`, `add_inc.shtml` |
| `data/<party>.txt` (107) | line 1 = party title; then one line per registered person: `Handle / Group1^Group2<BR>`, **kept sorted** (case-insensitive compare of the stored line); groups as `- Group (12 ppl)` | `parties_come.shtml` (POST) | `parties_come.shtml` (GET) |
| `data/#onliner.txt` | records of **5 lines**: `dd/mm/yy - hh:mm:ss`, user agent, reverse host (or IP), message, IP. Max 5 records (ring) | `index.shtml` | `index.shtml` |
| `data/#onliner_hist.txt` | same 5-line records, append-only: the record that falls out of the ring | `index.shtml` | nothing |
| `data/#onliner_french.txt` | one French word per line (dictionary used to reject French messages) | by hand | `isFrench_()` |
| `data/#onliner_blacklist.txt` | one insult word per line | by hand | `isFrench_()` |
| `data/#countries.txt` | alternating lines `tld` / `country name` | by hand | country lookup (code is commented out in `index.shtml`, live in `stats.shtml`) |
| `data/#host2country.txt`, `#ip_france.txt`, `#reverse.txt` | host-suffix to country pairs; `start-end` IP ranges as 32-bit integers (France); IP/host/nick triples | by hand | `stats.shtml` |
| `data/#stats.txt` | 4 lines per hit: `date`, `time`, `IP`, `user agent` (absent from the repo) | `stats.inc` | `news/stats.shtml` |
| `data/#stats_lastip.txt` | last 10 IPs, to count a visitor only once in a row (absent) | `stats.inc` | `stats.inc` |
| `<BASEPATH>\<yyyymmddhhmmss>.txt` (`g:\ojuice`, outside the tree, absent) | line 1 = pseudo, rest = text | `to99_live/add.shtml` | `to99_live.shtml`, newest first |
| `ctw/ricochet.log` | `date time IP referer target status` | citeweb.net redirector | nobody |

`stats.inc` is not included by any page in the repo (the platform probably prepended it to every page, which is how
hits were logged). `news/stats.shtml` is syntactically damaged in this copy (`Fi;hName =8Request...`, `fs/OpenTextFile`,
`% abbr`): it could not run as is.

## 3. Page by page

| Page (raw tree) | Reads | Writes | Interactive part | Static output (`site/`) |
|---|---|---|---|---|
| `index.shtml` | `data/#onliner*.txt`, `#countries.txt` | `#onliner.txt`, `#onliner_hist.txt` | banner rotation (`TabBanners` array, 3 entries: `ltp3_ban.gif`, `d2-banner02.gif`, `ltp3_ban.gif`); **onliner**: POST `inputzone` (>2 chars). Rules: `morglub` empties the list (admin); reject French (dictionary hit), blacklisted words, some HTML tags, and a second message from the same IP in a row; keeps the last 5, pushes the oldest to history | `index.html`: first banner, the 5 messages with their date (host/user agent/IP omitted), form replaced by a notice |
| `e|f/index.shtml` | `keywords*.inc` via `$$INC$$` | | frameset only (menu + `news.shtml`) | `e|f/index.html` |
| `e|f/news.shtml` | `news/<l>_index.txt`, news files, `logins.txt` | | first **9** news `[dd Mon yy]` + `mailto:` author; search form (citeweb) | `news.html` with the 9 news rendered; form replaced by a notice |
| `e|f/newsold.shtml` | same | | all news after the 9th `[dd/mm/yy]`, then a hand-written tail of older news | `newsold.html` |
| `news/disp_cur.shtml`, `disp_old.shtml` | | | include fragments | not emitted |
| `news/adde.shtml`, `addf.shtml`, `add_inc.shtml` | `logins.txt` | news file, index, e-mail | staff login + preview + save | not emitted (excluded by audit) |
| `news/logins.shtml` | `logins.txt` | | public list of newswriters with mailto | not emitted (excluded by audit) |
| `news/stats.shtml` | `#stats.txt`, `#ip_france.txt`, `#countries.txt`, `#host2country.txt` | | visitor statistics: browser, computer, OS shares, country, visits/day | not emitted (private; data not in the repo) |
| `birthdates.shtml` | **hard-coded arrays** (`TabNames`, `TabLinks`, `TabJours`, `TabMois`, `TabYears` = year-1900, `TabUIN`; 921 entries sorted by month/day; `MAJDate` = 06 September 1999) | | "Today" and "Next days" boxes computed from the server clock | `birthdates.html`: all 921 birthdays by month (the clock-based boxes cannot be static); links to `handles/<x>.htm` unchanged |
| `parties_come.shtml?party=<x>` | `data/<x>.txt` | `data/<x>.txt` (POST) | list + form to add oneself. The form only appeared when the HTTP referer was one of the site's own pages (`OJUICE_URLS`). Duplicate test: handle reduced to `[A-Z0-9]`; error codes 1 ok, 2 duplicate, 3 past party (unused); after POST a JS redirect to `?party&err&hndl` | one `parties_come_<x>.html` per `data/<x>.txt` (107) + `parties_come.html` (index). Four hand-made `parties_come_<x>.htm` also exist and are untouched |
| `to99_live/to99_live.shtml`, `add.shtml` | entries (absent) | entries | live report of TakeOver 99, entries added through a password-gated form | `to99_live.html` (empty list; the entries were not preserved); `add.shtml` not emitted |
| `handles_/handle.shtml?n=<nick>` | **MS SQL** DSN `OJUICE`, table `ojuice`, 36 columns | none | member sheet. Columns by position: nick, groups, picture id, ICQ uin, interview, country, sex, birth d/m/y, last/first name, e-mail, homepage, functions, status (0 active, 1 inactive, else retired), department, dept number, computer, scene since, IRC channels, IRC net, parties, favourite party/demo/group/coder/musician/graphician/art/movie/music, 2 "fun" answers, picture credit | not emitted (credentials in source, no data). The sheets were exported to `handles/<nick>1.htm` and lists `handles/handle_a..z.htm` (static, already in the repo) |

Pre-existing static exports of dynamic pages: `handles/*.htm` (1076), `groups/*.htm`, `parties_come_{buenzli99,rts98,slach98,wired98}.htm`,
`stats.htm`, `stats2.htm` (visitor dumps, excluded by audit).

### Counters, "onliner", banners, stats in one line each
- **Banner**: `Application` counter, +2 per page view over a 6-entry array; static = first banner.
- **Onliner**: 5-message ring + unbounded history; 25 lines on disk now (= 5 messages).
- **Counters/stats**: `stats.inc` (hit log) and the `$$CPT$$` macro (menu hit counter) were platform side; no value survives.
- **News**: index file prepended on each add; date comes from the file name `<lang>_yymmdd-hhmmss.txt`.

## 4. Forms (all removed in the static site)

| Form | Target | Status |
|---|---|---|
| `onliner` in `index.shtml` | itself (POST) | removed |
| `come` in `parties_come.shtml` | itself (POST) | removed |
| add news / login (`news/add_inc.shtml`) | itself (POST) | page not published |
| `to99_live/add.shtml` | itself (POST) | page not published |
| `searchengine` (29 pages: news pages, handle lists) | `citeweb.net/cgi/search.htm` | removed |
| `PseudoNew`, `error404`, `/ou/index.htm`, irc chat form | `formmail.cgi/.dll`, `/ou`, `irc.citeweb.net` | removed (pages `pseudonew*.htm`, `addgroup.htm` are excluded by the audit, links to them are turned into plain text) |

## 5. Known quirks the renderer deals with
- Path traversal in `parties_come.shtml?party=` (`FN` concatenated into a path), SQL injection in `handle.shtml?n=` (string concatenation): irrelevant once static.
- The onliner accepted some raw HTML (`<SCRIPT>` was not in the forbidden list): messages are HTML-escaped in the static page.
- `MonthName()` follows the server locale: French month abbreviations are assumed for `f/` pages (`sept.`, `août`), English for `e/` (not verifiable from the sources).
- `birthdates`: 921 entries; the original showed 9 upcoming days, the static page shows the whole year.
- Pages detect `top==self` and jump to `e|f/frames.htm?<path>` (frame-busting in reverse): kept, works statically.
- Windows paths are case-insensitive, Bunny storage is not: see `verify_build.py` "case-only" count (13, all in `demos/wired97/ansi.htm`-like pages).

## 6. Static output conventions (`scripts/statify.py`)
- Canonical names: `X.shtml` -> `X.html` (served as `text/html` by extension on Bunny; `.shtml` is not guaranteed to be). Every internal link is rewritten. Old URLs: `build-meta/redirects.tsv` (old path[+query] to new path, 116 rows) is meant for Bunny Edge Rules (301). `--shtml-stubs` additionally writes meta-refresh pages at the old names (fallback, only useful if `.shtml` is served as html).
- `index.shtml` -> `index.html`, so `/`, `/e/`, `/f/` work as directory indexes.
- Links to the dead host `ojuice.citeweb.net` become root-relative when the target exists in the archive; `ricochet?n;URL` links become direct links; ICQ presence badges are dropped; `$$...$$` macros resolved (`$$INC` pasted, others removed).
- Every html page declares `iso-8859-1`; pages that really are UTF-8 are left alone (none today).
- Exclusions: `scripts/exclude-globs.txt` + `docs/exclude-globs.txt`. They apply to what is **published**; excluded sources can still be read as render input (e.g. `news/logins.txt` for author names, `data/#onliner.txt` for the 5 messages). Links to excluded pages are replaced by their label.
- Privacy (owner decisions, **on by default**): nothing visible is dropped, it is masked with `????` keeping the original layout (see §8). Opt-outs: `--birthdates-year`, `--keep-emails`. Optional: `--strip-emails` (news authors), `--onliner-meta` (real browser/host, not recommended).

## 7. Overlay of newer files (2026-11-22)
Newer copies from another archive replaced the raw `birthdates.shtml` (last update 12 September 1999, 1002 entries instead of 921), `demos/volcanic3/volcanic3.htm` and `demos/p2b5/fgfx.htm`.
The new `birthdates.shtml` has damaged arrays: 3 entries with month `0` (first entries) and 8 trailing entries with garbage months (12592, 12598, ... 29238, text bytes read as numbers). Their dates cannot be recovered,
so `statify.py` lists the 11 under "Date unknown" (`[??/??]`) instead of crashing. Entries whose `handles/<x>.htm` sheet does not exist are listed without a link (no new broken links). Both cases are printed as warnings.

## 8. Privacy pass on the build (2026-11-22)
Principle: what visitors could see stays in place, redacted with the placeholder `????`; what they never saw stays excluded.

| Category | Where | Original | In the build |
|---|---|---|---|
| Birth date | `birthdates.html`, `handles/<nick>1.htm` (~1046 sheets; label `(dd/mm/yyyy)` kept) | `02/03/1971` | `02/03/????` (empty `.../.../...` left as is) |
| E-mail | all html and txt (982 distinct addresses before, 0 real after) | `<a href="mailto:x@y.z">x@y.z</a>` | text only `x@????` (link removed; any `mailto:` removed) |
| Onliner browser + host | `index.html`, last 5 messages | `[date - browser - host]` | `[date - ???? - ????]`; message/nick kept (HTML-escaped); IP was never shown; form replaced by "archived" notice |
| News authors | `e|f/news.html`, `newsold.html` | name linked with `mailto:` | name, no link (address never printed) |
| Visitor stats dumps | `stats.htm`, `stats2.htm` (republished by owner decision) | list of unknown visitor hosts/IPs, one per line (868 lines, 203 IPv4 each file) | each line `????` (1736 lines masked in total, 0 IPv4 left); country/browser/OS tables untouched; short browser-name lists kept |
| Birthday month damaged in source | `birthdates.html` | n/a | `[??/??]` under "Date unknown" (source data, not a redaction) |

Not redacted, on purpose: event/article dates (`groups/`, `e/`, `f/`, `news/`, `archive/`); ICQ UINs as text (badge `<img>` from the dead host is dropped, user OK); 5 IPv4 that are hosts of 1990s servers inside `http://` URLs (`groups_s|t|u.htm`, `linksparties.htm`, `webcams.htm`); nick decorations containing `@` (`Hi$c@n`).
Stays excluded (audit globs): `news/logins.txt` and `logins.shtml`, `data/#*` (onliner history, reverse DNS, IP tables), logs, ASP sources and includes.

**Onliner**: shown on the home page `index.html` under the banner, last 5 messages, no form. 
**Gate** (`scripts/verify_build.py`, audit "Order of operations" step 4): fails on `<%`, `%>`, `#include`, `$$` macros, VBScript/`OpenTextFile`, `<form`, `logins`, real e-mail patterns (`x@????` passes), `mailto:`, a numeric birth year in a handle sheet or in `birthdates.html` (`????` passes), forbidden file types, excluded files, missing charset, new broken links, incomplete build, IPv4 outside a URL host.

## 9. Regenerating
- The **raw originals live outside the repo**: `../OrangeJuice98-private/site-raw/` (override with `$OJ98_RAW`). They contain credentials (`news/logins.txt`, ASP sources), visitor logs and e-mails. They must never be copied into the repo, committed or pushed.
- `site/` in the repo is the **published tree**: the cleaned, redacted output of `scripts/statify.py`, committed and deployed as is. Never edit it by hand; fix `statify.py` and regenerate.
- Regenerate (empties `site/` first, applies the exclusion globs so no sensitive file is written):
  `python3 scripts/statify.py` then `python3 scripts/verify_build.py`.
- `verify_build.py` checks `site/` alone (this is the CI gate; no raw tree needed). When the raw tree is present it also compares broken links with the original.
- `build-meta/` (`redirects.tsv`, `excluded.txt`) holds only paths, no content, and is committed.
