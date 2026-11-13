# video-jobs-feed

An RSS feed of video jobs for Inoreader:
- Roles: social media video producer, video producer, video editor, videographer, motion designer, multimedia producer.
- Location: New York City, or remote with US eligibility. Hybrid roles only count when they're in NYC.
- Starred items (★): nonprofit, advocacy, civil rights, immigration, Latino-serving and other mission-driven employers.

Feed URL: https://thevoterthink.github.io/video-jobs-feed/feed.xml

## How it works

1. `build_feed.py` fetches the sources listed in `config.json`. It keeps titles that match the role list, drops anything outside NYC or remote-US, drops posts older than 30 days, and merges duplicates. When the same job is on two sources, the employer's own listing wins. The item notes the other sources it was found on.
2. It writes `docs/feed.xml`. It only rewrites the file when something changed.
3. `.github/workflows/update-feed.yml` runs the script three times a day (morning, midday, evening) and commits only when the feed changed.
4. GitHub Pages serves the `docs/` folder, which gives the feed its public URL.

`data/state.json` records the date each job was first seen, so dates stay stable between runs. If a source is down on a given run, its jobs from the last run are carried over.

## Sources

| Source | How | Why it's here |
|---|---|---|
| Idealist | Public NYC and national category pages (HTML) | Main nonprofit board |
| HigherEdJobs | Official RSS (categories 175, 37, 17) | NYC universities and CUNY |
| Himalayas | Public JSON API | Remote video roles, US-eligible |
| Remotive, Remote OK, Jobicy, Working Nomads | Public JSON APIs | Remote roles (low yield) |
| We Work Remotely | Official RSS feeds | Remote roles (low yield) |
| NYC Gov | NYC Open Data "Jobs NYC Postings" dataset (JSON) | City agency jobs, which otherwise only show up on LinkedIn. Unstarred. |
| Greenhouse, Lever, BambooHR, Breezy, Pinpoint, Ashby | Employers' public job-board APIs (JSON) | A watchlist of about 100 employers: mission-driven ones (starred), plus media, tech and consumer companies and Gibson Dunn (unstarred) |
| Workday | Employers' public Workday search API (JSON) | WNET, plus law firms Skadden, Simpson Thacher, White & Case |
| Jobvite | Employer's public job list (HTML table) | New York Public Radio (WNYC) |
| CUNY | cuny.jobs sitemap (title and borough from each job URL) | CUNY postings that HigherEdJobs misses |

Law firm and for-profit boards carry `"mission": false`, so their jobs show up without a ★. If one employer's board stops answering, the run skips it and keeps the rest.

CUNY: robots.txt blocks the cuny.jobs RSS and JSON feeds, so the feed reads the public sitemap instead. Job pages need JavaScript, so CUNY items show "CUNY" as the employer and the first-seen date. The sitemap refreshes every few days, so CUNY jobs can show up late.

Not used: LinkedIn and Indeed (no public API, and they block automated access); NYFA and NYSAIS (lists load by JavaScript, no feed); Public Media Jobs, the LMA career center, MoMA and Kirkland & Ellis (Cloudflare bot checks); nypl.org (bot check; NYPL is read from its Pinpoint board instead); Paul Weiss (Taleo), Latham (iCIMS), NYCLU (Paylocity), Legal Aid Society (ADP) and BAM (Paycom), which have no public job API.

## Changing things (no code needed)

Everything is in `config.json`:
- **Add an employer:** find its job board. If the address contains `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `<slug>.bamboohr.com`, `<slug>.breezy.hr`, `<slug>.pinpointhq.com`, `jobs.ashbyhq.com/<slug>` or `jobs.jobvite.com/<slug>`, add `"<slug>": "Display Name"` under the matching source. To leave its jobs unstarred, use `"<slug>": {"name": "Display Name", "mission": false}`.
- **Add a Workday employer:** the address looks like `https://<tenant>.wd5.myworkdayjobs.com/<site>`. Add `{"name": "...", "host": "<tenant>.wd5.myworkdayjobs.com", "tenant": "<tenant>", "site": "<site>"}` under `workday.boards`. Small boards can use `"queries": [""]` to read every job.
- **Widen or narrow roles:** edit `role_include` and `role_exclude`. These are plain phrases matched against job titles.
- **Hide remote jobs from for-profit companies:** set `"remote_only_if_preferred": true`.
- **Block a noisy company:** add its name to `exclude_orgs`.
- **Mark more employers as preferred (★):** add words to `preferred_keywords`.

Test locally with `python3 build_feed.py --dry-run`. Nothing is written, and it prints a per-source report. Run the offline checks with `python3 -m unittest`.

## If something breaks

Open the Actions tab and click the latest run. The "Build feed" step prints a table with one row per source.
- `ERROR` on one row: that source changed or is down. The feed keeps last run's jobs from it. Idealist is the most likely to break, because it's read from page HTML.
- GitHub turns off scheduled workflows after 60 days with no repository activity. If that happens, click "Enable workflow" in the Actions tab.
