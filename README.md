# video-jobs-feed

An RSS feed of video jobs for Inoreader:
- Roles: social media video producer, video producer, video editor, videographer, motion designer, multimedia producer.
- Location: New York City, or remote with US eligibility. Hybrid roles only count when they're in NYC.
- Starred items (★): nonprofit, advocacy, civil rights, immigration, Latino-serving and other mission-driven employers.

Feed URL: https://thevoterthink.github.io/video-jobs-feed/feed.xml

## How it works

1. `build_feed.py` fetches the sources listed in `config.json`. It keeps titles that match the role list, drops anything outside NYC or remote-US, drops posts older than 30 days, and merges duplicates. When the same job is on two sources, the employer's own listing wins. The item notes the other sources it was found on.
2. It writes `docs/feed.xml`. It only rewrites the file when something changed.
3. `.github/workflows/update-feed.yml` runs the script twice a day and commits only when the feed changed.
4. GitHub Pages serves the `docs/` folder, which gives the feed its public URL.

`data/state.json` records the date each job was first seen, so dates stay stable between runs. If a source is down on a given run, its jobs from the last run are carried over.

## Sources

| Source | How | Why it's here |
|---|---|---|
| Idealist | Public NYC and national category pages (HTML) | Main nonprofit board |
| HigherEdJobs | Official RSS (categories 175, 37, 17) | NYC universities and CUNY |
| Himalayas | Public JSON API | Remote video roles, US-eligible |
| Remotive, Remote OK | Public JSON APIs | Remote roles (low yield) |
| Greenhouse, Lever, BambooHR | Employers' public job-board APIs | A watchlist of mission-driven employers |

Not used: LinkedIn and Indeed (no public API, and they block automated access), NYFA, Public Media Jobs and the LMA career center (Cloudflare bot checks), and CUNY's own site (its robots.txt disallows the feed).

## Changing things (no code needed)

Everything is in `config.json`:
- **Add an employer:** find its job board. The address will contain `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>` or `<slug>.bamboohr.com`. Add `"<slug>": "Display Name"` under the matching source.
- **Widen or narrow roles:** edit `role_include` and `role_exclude`. These are plain phrases matched against job titles.
- **Hide remote jobs from for-profit companies:** set `"remote_only_if_preferred": true`.
- **Block a noisy company:** add its name to `exclude_orgs`.
- **Mark more employers as preferred (★):** add words to `preferred_keywords`.

Test locally with `python3 build_feed.py --dry-run`. Nothing is written, and it prints a per-source report. Run the offline checks with `python3 -m unittest`.

## If something breaks

Open the Actions tab and click the latest run. The "Build feed" step prints a table with one row per source.
- `ERROR` on one row: that source changed or is down. The feed keeps last run's jobs from it. Idealist is the most likely to break, because it's read from page HTML.
- GitHub turns off scheduled workflows after 60 days with no repository activity. If that happens, click "Enable workflow" in the Actions tab.
