# OrangeJuice98 - pre-publication security audit

Scope: the raw original tree, now `../OrangeJuice98-private/site-raw/` (3094 files, 53 MB; it was `site/` when audited; outside the repo since 2026-11-22; the repo's `site/` is the redacted output of `scripts/statify.py`), read-only audit, 2026-11-22. Nothing was modified or executed.
Secret values are deliberately NOT reproduced here.

## Summary of the tree

| Dir | Files | Notes |
|---|---|---|
| `handles/` | 1385 | 1075 `.htm`, 288 `.jpg`, 21 `.gif`, 1 `.ini`; static member pages |
| `news/` | 262 | 254 `.txt` news items (`e_*`/`f_*`), 7 `.shtml` (ASP), `add.htm`, `logins.txt` |
| `data/` | 115 | party data + visitor logs |
| `groups/` | 60 | static pages |
| `to99_live/` | 2 | ASP (read + write) |
| `demos/` | - | 34 MB, mostly rar/zip/jpg |

Charset: all pages declaring one use `iso-8859-1` (265). 17 pages use `<frameset>`. 1279 inline `<script language="javascript">`, no external script.

## Findings (prioritized)

### P0 - secrets
1. **`news/logins.txt`**: 12 newswriter accounts (login, **plaintext password**, name, e-mail), 48 lines. Directly downloadable as static file. DELETE. Passwords were real; assume reused elsewhere.
2. **`handles_/handle.shtml:24`**: ASP source with hardcoded MS SQL connection (DSN `OJUICE`, user `sa`, plaintext password). DELETE the whole `handles_/`.
3. **`to99_live/add.shtml:20`**: hardcoded write password (5-char word) guarding file creation. DELETE.
4. `news/add_inc.shtml`: hardcodes 2 personal e-mails as mail recipients, and an HTML hidden field echoes the user's password (`Passwd`). DELETE.

### P1 - personal data
5. **`data/#onliner_hist.txt`** (434 KB, 15 215 lines): visitor log: timestamp, user agent, reverse hostname, free-text message/nick, **IPv4**, roughly 3000 entries (1998-99). DELETE.
6. `data/#onliner.txt` (25 l), `#onliner_french.txt` (182 l, hosts/IPs of French visitors + nicks), `#onliner_blacklist.txt` (21 l: insult word list, harmless but not content), `#reverse.txt` (103 l IP -> hostname -> nick), `#ip_france.txt` (93 l IP/host/nick, French members' ISP hosts), `#host2country.txt`, `#countries.txt` (reference tables, no PII; excluded as they only serve the ASP). DELETE all (`data/#*`).
7. **`ctw/ricochet.log`** (120 lines): web-server referrer log with visitor IPs. DELETE, and `ctw/404.txt|htm` (error-form config for dead formmail).
8. **`stats.htm`, `stats2.htm`**: 203 IPv4 each + ISP hostnames (static dumps of visitor stats). DELETE (or redact IP columns if the stats are wanted).
9. `.picasa.ini` x4 (`handles/pictures`, `demos/ltp2`, `demos/takeover98`, `demos/volcanic3`) and `.DS_Store`: harmless metadata. DELETE.
10. **E-mails in published content**: 1002 distinct addresses in total. `handles/` 848 (1046 files with `mailto:`, member pages), `groups/` 34, `data/` 36, `archive/` 8, `demos/` 5, `news/*.txt` 16 (30 files). Plus top-level static pages with addresses (`parties_*`, `webcams`, `pc_megademo_project`). These are 1998-99 addresses of identifiable people (spam harvesting + GDPR). Recommendation: **redact** `@domain` -> keep nick only, or obfuscate (`name [at] ...`), via a build step in the static-ification session. Decision needed from owner; pages are intentionally public member listings but 27 years old.
11. ICQ presence badges: 334 pages embed `online.mirabilis.com/scripts/online.dll?icq=<UIN>&img=5` (615 hits, host dead). Reveals ICQ UINs, requests to a dead host. Strip the `<img>` (UIN may stay as text).
12. `birthdates.shtml` (46 KB, ASP) - generates birthday list of members from a DB; check output before publishing. Excluded as ASP source (static-ify offline, see 2).

### P1 - server-side code that must never be served raw
13. 18 files contain `<% %>`: `index.shtml`, `parties_come.shtml`, `birthdates.shtml`, `stats.inc`, `handles_/handle.shtml`, `to99_live/{add,to99_live}.shtml`, `e/{news,newsold}.shtml`, `f/{news,newsold}.shtml`, `news/{logins,adde,addf,disp_cur,disp_old,add_inc,stats}.shtml`.
    - Entire `.shtml` set (19 files) leaks FileSystemObject paths (`\ojuice\news`, `data\#stats.txt`) if served raw. Only publish the **rendered** output of the pages that are public (`index`, `e/news`, `f/news`, `e|f/newsold`, `parties_come`, `to99_live`, `birthdates`).
    - `#include file="../news/{disp_cur,disp_old,add_inc}.shtml"` in 6 pages: must be resolved at static-ification, then removed.
    - `.inc` (4 files: `keywords.inc`, `keywords2.inc` = 2 META keyword strings, `stats.inc` = ASP writing visitor logs, `demos/title.inc`). Inline then delete.

### P1 - write paths (all must disappear)
| File | Write |
|---|---|
| `index.shtml` | `CreateTextFile` (l.148), `OpenTextFile(...,8)` append (l.212): "onliner" guestbook (POST form `name=onliner`) writing `data/#onliner*.txt` |
| `stats.inc` (included by index) | append to `data/#stats.txt`, `data/#stats_lastip.txt` (logs every visitor IP) |
| `news/add_inc.shtml` | `CreateTextFile` news `.txt` + index; login check against `logins.txt`; sends mail via PipeMail |
| `to99_live/add.shtml` | `CreateTextFile` live-report entry (password-gated) |
| `parties_come.shtml` | POST form `come` (reads/writes party data, `data/*.txt`) |
| `handles_/handle.shtml` | SQL read of member DB (no write seen, but DB connection) |
| `news/stats.shtml` | reads logs (private stats page) |
Dead third-party endpoints, forms POSTing to `citeweb.net` (host dead): `pseudonew.htm`/`pseudonew2.htm` (`v19.citeweb.net/cgi/formmail.cgi`), `ctw/404.htm` (`citeweb.net/cgi/formmail.dll`), `form_*.htm` result pages, `addgroup.htm`; 30 search forms `www|pom.citeweb.net/cgi/search.htm` (31 forms across pages), `irc.citeweb.net/chat/index.htm` (`chat.htm`), `/ou/index.htm` (4 forms, local POST - will 405/404 on a CDN). On a CDN a POST to a static file is rejected; these forms must be neutralized (remove `<form>` or replace by a "read-only archive" note) in the static-ification step.

### P2 - binaries and archives (no execution)
Nothing is ever executed server-side or client-side. Risk = serving untrusted legacy files with a guessable `Content-Type`; set `X-Content-Type-Options: nosniff` and `Content-Disposition: attachment` for archives.
- `.rar` x5: `demos/tg97_fli.rar` 2.6 MB, `demos/abduction97/bubble.rar` 416 KB, `demos/asm97/asm97_gfx4.rar` 468 KB, `demos/p2b5/p2b5_gfx1.rar` 400 KB, `demos/p2b5/p2b5_ray3.rar` 304 KB.
- `.zip` x8 (incl. `archive/px980923.zip` 88 KB, `demos/p2b5/pbzip.zip` 12 KB, `demos/wired98/wir98res.zip` 5 KB, ...): demoscene content from 1997-98, not scanned for malware (no AV tool run); contents untrusted. Recommend ClamAV scan in CI (`clamscan -r site/` (published tree)) rather than deletion.
- `.mts` x11 in `pictures/` (3D model files, 14-220 KB each), `.mdz` x2 (`archive/ojtheme.mdz` 28 KB, `archive/pressme.mdz` 14 KB, tracker modules/themes), `f/Techno78.mid` (33 KB, MIDI).
- Static-only; keep. No `.exe`/`.dll`/`.bat` present. Greps for `<object>`, `<embed>`, `vbscript`, `classid`, `applet` hit only text/links in ~12 pages (e.g. `metastream.htm`, `rc5.html`): check `metastream.htm` (embedded streaming plugin) and drop the embed.
- Size: 53 MB total, well below any CDN limit.

### P2 - external dependencies
- Hardcoded absolute links: `http://ojuice.citeweb.net` 2616x, `www.citeweb.net` 926x, other `*.citeweb.net` subdomains (ribbon 16, defcon 9, ...). Host is dead: links 404/NXDOMAIN. Rewrite `ojuice.citeweb.net` -> relative or the new domain; leave others (or mark as archived).
- 7511 `http://` references, no `https://`. All plain HTTP: mixed content blocked if the site is served over HTTPS (images from `http://www.theparty.dk` 7, `pixel.scene.org` 2, `multimania.com`, `planet-d.net`, etc: 345 pages have `src="http..."`). Dead hosts mostly; images will break silently; no security issue. Strip or leave.
- Third-party JS: none loaded from outside (no `<script src=...>`). 1279 inline scripts only (rollovers, `verif_form`, `history.back()`): CSP needs `'unsafe-inline'` for `script-src`.

## Recommended CDN headers (bunny.net Edge Rules)

Content is iso-8859-1 + frames + inline JS/CSS + remote `http` images: keep CSP permissive but still blocks the real risks (forms to other hosts, plugins, framing by others).

```
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: http: https:; media-src 'self'; font-src 'self'; object-src 'none'; frame-src 'self'; frame-ancestors 'self'; form-action 'none'; base-uri 'self'; connect-src 'none'
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()
X-Frame-Options: SAMEORIGIN
Strict-Transport-Security: max-age=31536000
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
```
Notes:
- `img-src http:` is needed only while legacy `http://` images remain; drop it if they are rewritten.
- `form-action 'none'` also stops any remaining dead form from posting off-site.
- Do not force `Content-Type: text/html; charset=utf-8`: serve `.htm/.shtml` as `text/html; charset=iso-8859-1` (or convert files to UTF-8 and the meta tags together). `.shtml` needs an explicit `text/html` mapping on bunny (storage serves by extension; verify).
- `.txt` as `text/plain; charset=iso-8859-1`; `.rar/.zip/.mdz/.mts/.mid` as `application/octet-stream` + `Content-Disposition: attachment`.
- Block methods other than GET/HEAD (Edge Rule: return 405).
- Add `/robots.txt`/`X-Robots-Tag` per owner preference (the site holds personal names).

## Order of operations
1. Static-ification session renders public ASP pages offline (from a copy), then removes ASP/inc.
2. Apply `docs/exclude-globs.txt` at upload (and delete from the repo history if the repo is public: files are in git history; use a fresh repo or `git filter-repo` for `news/logins.txt`, `handles_/`, `data/#*`, `ctw/ricochet.log`, `stats*.htm`).
3. Redact e-mails/ICQ badges; neutralize forms; rewrite `ojuice.citeweb.net`.
4. Post-build gate in CI: fail if `grep -rE '<%|#include|logins|OpenTextFile|\b[0-9]{1,3}(\.[0-9]{1,3}){3}\b'` hits the build output; ClamAV scan.
5. Rotate/forget: the SQL `sa` password and the news passwords are in this repo's history if committed: consider them compromised.
