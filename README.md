# OrangeJuice98

**Orange Juice (1998 version).** Orange Juice was a French-language online magazine about the demoscene (demoparty reports and calendar, demos and intros, groups and handles, artwork, links), here in its 1998 edition, with its frames-based layout of the time.

Archive of an old dynamic website (SSI, ASP-style forms, guestbook-like scripts), published
as a **100 % static, read-only site** on bunny.net at the hostname of [`site.json`](site.json).
Kept online for history, no longer developed. Visitors can never write anything: the pages
are the cleaned, redacted static pages committed in `site/`, the CDN only serves files, and every method
except GET/HEAD/OPTIONS is refused at the edge.

## Search engines

`scripts/seo.py` writes `robots.txt` (allow everything, with the `Sitemap:` line) and `sitemap.xml` (indexable pages only) into `site/`; `statify.py` runs it at the end of the build (alone: `python3 -I scripts/seo.py`).

`scripts/frame-restore.js` (one `<script>` tag in every page, added by `seo.py`) rebuilds the frames around a page opened on its own (a search result): it goes to `/f/index.html#p=<page>` (`/e/` for the pages of `/e/`), and the framesets (the menu one, then a demo's own) load it into the right frame. It replaces the original `if (top == self)` snippet with its `confirm()` language prompt, which `statify.py` drops. The page stays a plain file, so crawlers still read it.

## Layout

| Path | Content |
| --- | --- |
| `site/` | The cleaned, redacted static site, exactly as served (the raw originals are kept outside the repository) |
| `hosting/404.html` | Custom 404 page, deployed next to `site/` |
| `site.json` | The only site-specific settings: repository, storage zone, pull zone, hostname, 404 page |
| `infra/` | OpenTofu: storage zone, pull zone, hostname, hardening edge rules (state encrypted, committed) |
| `infra/secrets.env` | Proton Pass **references** to the secrets (vault `OldSites`), no secret values |
| `scripts/statify.py` | Raw originals (outside the repo) → cleaned `site/` (run locally) |
| `scripts/verify_build.py` | Checks `site/`: no ASP/SSI, forms, secrets, broken links |
| `scripts/check_excludes.py` | Fails if `site/` holds a path of `scripts/exclude-globs.txt` |
| `scripts/deploy.py` | Sync `site/` to the storage zone, purge the CDN |
| `scripts/smoke_test.py` | Check the public site: pages as in `site/`, POST refused, security headers, charset, 404 |
| `scripts/bootstrap.sh` | One-time setup (`new` for this site, `github` for the repository and secrets) |
| `docs/` | Security audit of the original site |

DNS is not managed here: the hostname is a CNAME to `<pull zone>.b-cdn.net` at Infomaniak.

Full list of the sites: https://chanoj.github.io/

## What was changed from the original

The site was rebuilt from classic ASP (`.shtml` files reading flat `.txt` files, no SQL) into
plain HTML; the reverse-engineered data model is in [`docs/data-model.md`](docs/data-model.md).

- Dynamic pages rendered once from the data; forms, counters and admin pages removed (a short
  "archived, read-only" notice replaces them).
- Personal data redacted, keeping the original layout: birth years (`26/12/????`), e-mail
  domains (`nick@????`), onliner browser/host fields, visitor IPs and hosts in the stats pages.
  Logins, logs and ASP sources are never published (`scripts/exclude-globs.txt`).
- E-mail addresses on the people pages (`handles/`, `groups/`): too many to sort by hand, so every address keeps
  its domain and shows only the first and last letter of the local part (`l******t@domain.com`).
- E-mail addresses elsewhere: the domain is masked (`local@????`) only when the part before the `@` holds a family
  name. A handle (the page's own, or any handle or group of the site), a role address and a first name keep
  the full address; everything else is masked. Addresses kept whole are listed in `build-meta/kept-emails.txt`
  (`scripts/verify_build.py` allows exactly those), event and team addresses in `scripts/email-keep.txt`.
- Phone numbers (BBS lists, a job ad, an old news item) keep their prefix; the last four digits are `?`.
- ICQ: the UIN, its pager link and the "ICQ state :" label stay. The live presence badge (a dead host) is
  replaced by the site's own red offline flower (`pictures/icqoff.gif`, as in the legend), so no request goes to an ICQ server.
- External links keep their original `href` (only counters, forms and the old search engine are gone) and
  open in a new tab (`target="_blank" rel="noopener"`), as many hosts refuse to be framed.
- Links that were broken in the original are repaired (wrong paths, case, root pages that pointed outside the
  site) or lose their link when the target never existed (`LINK_FIXES` in `scripts/statify.py`).
  `view_img.htm` opened without a picture shows a note instead of a broken image.
- The two Modplug players of the menu (dead plug-in) are replaced by a small jukebox: see *Music* below.
- Third-party text files (demo results, `.nfo`) are copied byte-identical; the pages that link one that holds
  an address or phone number carry the removal footer.
- `f/hack.htm` (a 1999 prank page, linked from `f/newsold.html`) played `Techno78.mid` through the IE-only
  `<bgsound>`: it now loads `site/music/midi-player.js`, a small Web Audio MIDI player (no soundfont), with a
  play/stop button; the music starts at the first click or key press and loops.
- Links to `www.citeweb.net` point to https://citeweb97.sauthier.family, same path.
- `birthdates.html` is indexable.
- `demos/p2b5/pbzip.zip` (a stale source copy of the P2B5 pages, linked nowhere) is not published.
- `face="verdana"` gets sans-serif fallbacks (`DejaVu Sans, Geneva, sans-serif`) for systems
  without Verdana.
- `birthdates.shtml` and two demo pages come from a second copy of the site that had newer
  versions; it is merged into the raw tree and no longer needed.
- `webcam32.jpg`, a 1999 capture of the Orange Juice webcam, is added to the raw tree so that
  `webcams.htm` shows it from the site itself instead of the dead `ojuice.citeweb.net` host
  (the other webcams of that page are dead external images, left as they were).

## Regenerating `site/`

`site/` is generated: never edit it by hand, fix `scripts/statify.py` and regenerate. The raw
originals (with logins and logs) live **outside the repository**, in
`~/Code/OrangeJuice98-private/site-raw/` (or `$OJ98_RAW`); keep that folder, it is the only copy.

```bash
python3 scripts/statify.py && python3 scripts/verify_build.py
```

## Hardening at the edge (`infra/main.tf`)

- Edge rule 1: any method other than GET, HEAD, OPTIONS is blocked at the edge, before the
  origin (bunny.net answers 403; edge rules cannot return 405). `block_post_requests` is also on.
- Edge rule 2: `Content-Security-Policy` (same-origin, inline script/style allowed for the 1998
  HTML, `wasm-unsafe-eval` and `connect-src 'self'` for the menu jukebox, frames same-origin only, `form-action 'none'`), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN` (the frames layout still works), `Referrer-Policy`, HSTS,
  `Permissions-Policy`.
- Edge rules 3-4: `Content-Type` with `charset=iso-8859-1` for `.htm/.html/.shtml` and
  `.txt/.inc/.ini` (the pages are ISO-8859-1).
- Edge rule 5: browser cache 7 days for images and archives, 1 hour for pages; CDN cache 30
  days, purged on each deployment.
- Archives (`rar zip mdz mts mid`): `application/octet-stream` + `Content-Disposition: attachment`.
- COOP/CORP `same-origin`. Search engines are allowed (no `X-Robots-Tag`; `robots.txt` allows everything and `sitemap.xml` is served).
- CI (deploy workflow), before upload: excluded-paths check, `scripts/verify_build.py --build site` and a ClamAV scan of `site/`.
- Cookies are stripped before the origin; TLS "Compatible"; forced HTTPS.
- Custom 404: `hosting/404.html` (`not_found_page` in `site.json`).

## Music

The menu frame (`/f/ojmenu.htm`, `/e/ojmenu.htm`) had two Modplug plug-in players (`archive/ojtheme.mdz`,
`archive/pressme.mdz`, `autostart=false`). They are replaced by a 130x36 player: play/pause, next track, volume.
Nothing plays until the visitor clicks; the second track follows the first. `scripts/music/` holds the player
(`oj-player.js`) and chiptune3 with libopenmpt (copied from the LTP4
site; MIT, and BSD for libopenmpt, see `scripts/music/LICENSE.txt`); the build copies them to `site/music/` and
unzips the two modules there as `.xm` (the `.mdz` stay for download). WebAssembly needs the CSP change in
`infra/main.tf` (an infra apply after the merge).

## Removal requests

Pages that show personal data end with a small footer, "To be removed from this page, contact me." (a
`mailto:` link, 630 px wide, left-aligned like the pages). `scripts/statify.py` adds it on the pages with real names
(`is_people_page`), on pages where an address keeps a first name or is masked, and on pages that link a
third-party file holding addresses or phone numbers. Pages with handles only, role addresses or no personal data
have none. It survives rebuilds; `scripts/verify_build.py` leaves it out of its `mailto:` and e-mail checks.

## Deploying

- **Website**: push a change under `site/` or `hosting/` to `main` → workflow *Website: deploy*
  runs the checks, uploads the changed files, deletes the removed ones, purges the CDN and
  runs the smoke test. A manual run allows a dry run. While the storage zone does not exist
  (before the first infra apply), it deploys nothing and only reports a notice.
- **Infra**: pull requests touching `infra/` show the plan; apply is a manual run of
  *Infra: bunny.net* (action `apply`, from `main`), which commits the encrypted state, deploys
  `site/` and runs the smoke test.

GitHub secrets: `BUNNY_API_KEY` (bunny.net account API key) and `TF_VAR_STATE_PASSPHRASE`
(state encryption), both from Proton Pass vault `OldSites`.

Locally (Proton Pass CLI logged in):

```bash
pass-cli run --env-file infra/secrets.env -- python3 scripts/deploy.py --dry-run
```

```bash
cd infra && pass-cli run --env-file secrets.env -- tofu plan
```

## First setup (done on 2026-11-22, kept as a record)

1. Write `site.json` and validate offline (secrets are the shared ones of the other old sites: same Proton Pass references, `OrangeJuice97-tofu-state-passphrase`):

   ```bash
   scripts/bootstrap.sh new OrangeJuice98 orangejuice98 orangejuice98 orangejuice98.sauthier.family
   ```

2. Create the GitHub repository and its secrets, push, open the PR:

   ```bash
   scripts/bootstrap.sh github
   ```

3. After merge, run the *Infra: bunny.net* workflow with action `apply`: it creates the zones,
   hostname and edge rules, deploys `site/` and runs the smoke test.
4. At Infomaniak (Manager > Domaines > sauthier.family > Zone DNS), add a CNAME
   `orangejuice98` → `orangejuice98.b-cdn.net`. The certificate is issued once DNS answers;
   re-run the smoke test (*Monitoring: weekly smoke test*, manual run) after a few minutes.

## Manual steps in the bunny.net dashboard (only if the apply cannot do it)

If the apply rejects an edge rule or the 404 path, set it by hand (CDN > pull zone
`orangejuice98`):

1. *Edge rules* > *Add Edge Rule*: condition *Request Method* is none of `GET`, `HEAD`,
   `OPTIONS` → action *Block Request*.
2. *Edge rules* > *Add Edge Rule*: condition *URL* `*` → one *Set Response Header* action per
   header listed above (values in `infra/main.tf`, `local.csp` and the rule 2 block).
3. *Edge rules*: condition *URL Extension* `htm`, `html`, `shtml` → *Set Response Header*
   `Content-Type: text/html; charset=iso-8859-1`.
4. Storage > storage zone `orangejuice98` > *Error pages*: set the 404 file to `/404.html`.

## To Do

Cross-links to fix in a batch pass (the targets are archived on `sauthier.family`; rewrite the `href`s). Counts
are `href`/`src` attributes in `site/` (2026-11-24):

- `ojuice.citeweb.net`: no link left (the 3 handle homepages now point to `/`); the old address still shows as text
  in the `<title>` of most pages and in a few `alt`s.
- `*.citeweb.net` member sites (`arfparty` 6, `defcon` 6, `mentagen` 3, `froggy` 3, ...): Citeweb97 (https://citeweb97.sauthier.family), same paths. (`www.citeweb.net` is done.)
- `ribbon.citeweb.net` (16 links in 4 files): https://citeweb97.sauthier.family/ribbon/.
- `ltp.planet-d.net` (10 links in 7 files): LTP3 (https://ltp3.sauthier.family).
- `bomb.planet-d.net` (4 links in 4 files): Bomb98 (https://bomb98.sauthier.family).
- Other `*.planet-d.net` member sites (`trashcan` 3, `www.planet-d.net` 4, ...): to sort out.
