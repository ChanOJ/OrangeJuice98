# OrangeJuice98

**Orange Juice (1998 version).** Orange Juice was a French-language online magazine about the demoscene (demoparty reports and calendar, demos and intros, groups and handles, artwork, links), here in its 1998 edition, with its frames-based layout of the time.

Archive of an old dynamic website (SSI, ASP-style forms, guestbook-like scripts), published
as a **100 % static, read-only site** on bunny.net at the hostname of [`site.json`](site.json).
Kept online for history, no longer developed. Visitors can never write anything: the pages
are the cleaned, redacted static pages committed in `site/`, the CDN only serves files, and every method
except GET/HEAD/OPTIONS is refused at the edge.

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

## Hardening (pull zone, `infra/main.tf`)

- Edge rule 1: any method other than GET, HEAD, OPTIONS is blocked at the edge, before the
  origin (bunny.net answers 403; edge rules cannot return 405). `block_post_requests` is also on.
- Edge rule 2: `Content-Security-Policy` (same-origin, inline script/style allowed for the 1998
  HTML, frames same-origin only, `form-action 'none'`), `X-Content-Type-Options: nosniff`,
  `X-Frame-Options: SAMEORIGIN` (the frames layout still works), `Referrer-Policy`, HSTS,
  `Permissions-Policy`.
- Edge rules 3-4: `Content-Type` with `charset=iso-8859-1` for `.htm/.html/.shtml` and
  `.txt/.inc/.ini` (the pages are ISO-8859-1).
- Edge rule 5: browser cache 7 days for images and archives, 1 hour for pages; CDN cache 30
  days, purged on each deployment.
- Archives (`rar zip mdz mts mid`): `application/octet-stream` + `Content-Disposition: attachment`.
- COOP/CORP `same-origin`. Search engines are allowed (no `X-Robots-Tag`, no `robots.txt`).
- CI (deploy workflow), before upload: excluded-paths check, `scripts/verify_build.py site` and a ClamAV scan of `site/`.
- Cookies are stripped before the origin; TLS "Compatible"; forced HTTPS.
- Custom 404: `hosting/404.html` (`not_found_page` in `site.json`).

## Deploying

- **Website**: push a change under `site/` or `hosting/` to `main` → workflow *Website: deploy*
  runs the checks, uploads the changed files, deletes the removed ones, purges the CDN and
  runs the smoke test. A manual run allows a dry run.
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

## First setup (nothing exists on bunny.net yet)

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
