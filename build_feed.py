#!/usr/bin/env python3
"""Build an RSS feed of video producer / video editor jobs.

Standard library only. Reads config.json, fetches every enabled source,
keeps roles that match the title filters and are in NYC or remote (US),
removes duplicates across sources, and writes docs/feed.xml.

Usage:
    python3 build_feed.py            # fetch, build, write docs/feed.xml
    python3 build_feed.py --dry-run  # fetch and print a report, write nothing
"""

import email.utils
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
FEED_PATH = ROOT / "docs" / "feed.xml"
STATE_PATH = ROOT / "data" / "state.json"
NOW = datetime.now(timezone.utc)

FEED_CFG = CONFIG["feed"]
SRC_CFG = CONFIG["sources"]

# Higher number wins when the same job shows up on several sources:
# the employer's own listing is the best link to keep.
SOURCE_PRIORITY = {
    "Greenhouse": 90, "Lever": 90, "BambooHR": 90, "Idealist": 70, "HigherEdJobs": 60,
    "Himalayas": 40, "Remotive": 30, "Remote OK": 20,
}


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #

def _compile(terms, right_boundary):
    parts = []
    for t in terms:
        t = t.strip().lower()
        if not t:
            continue
        pat = r"(?<![a-z0-9])" + re.escape(t)
        if right_boundary:
            pat += r"(?![a-z0-9])"
        parts.append(pat)
    return re.compile("|".join(parts)) if parts else re.compile(r"(?!x)x")


ROLE_INCLUDE = _compile(CONFIG["role_include"], right_boundary=False)
ROLE_EXCLUDE = _compile(CONFIG["role_exclude"], right_boundary=False)
NYC = _compile(CONFIG["nyc_terms"], right_boundary=True)
US = _compile(CONFIG["us_terms"], right_boundary=True)
NON_US = _compile(CONFIG["non_us_terms"], right_boundary=True)
PREFERRED = _compile(CONFIG["preferred_keywords"], right_boundary=False)
EXCLUDE_ORGS = {o.lower() for o in CONFIG.get("exclude_orgs", [])}
TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(s):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", s or ""))).strip()


def norm(s):
    s = html.unescape(s or "").lower().replace("&", " and ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    words = [w for w in s.split() if w not in {"the", "inc", "llc", "co", "corp", "ltd", "org"}]
    return " ".join(words)


ORG_ALIASES = {norm(k): norm(v) for k, v in CONFIG.get("org_aliases", {}).items()}


def dedupe_key(title, org):
    o = norm(org)
    return norm(title) + "|" + ORG_ALIASES.get(o, o)


def role_ok(title):
    t = (title or "").lower()
    return bool(ROLE_INCLUDE.search(t)) and not ROLE_EXCLUDE.search(t)


def classify_location(loc, remote_flag=False, restrictions=None):
    """Return a short label ('NYC', 'Remote (US)', 'NYC / Remote') or None if out of scope."""
    text = (loc or "").lower()
    in_nyc = bool(NYC.search(text))
    remote = remote_flag or "remote" in text or "anywhere" in text
    if restrictions is not None:  # remote boards that list allowed countries
        joined = " ".join(restrictions).lower()
        us_ok = not restrictions or bool(US.search(joined))
    else:
        us_ok = bool(US.search(text)) or not NON_US.search(text)
    if in_nyc and remote and us_ok:
        return "NYC / Remote"
    if in_nyc:
        return "NYC"
    if remote and us_ok:
        return "Remote (US)"
    return None


def parse_relative(text):
    """'Posted 3 days ago' -> datetime."""
    t = (text or "").lower()
    if "yesterday" in t:
        return NOW - timedelta(days=1)
    m = re.search(r"(\d+|an?|one)\s+(minute|hour|day|week|month)s?", t)
    if not m:
        return NOW if ("today" in t or "just" in t) else None
    n = 1 if m.group(1) in ("a", "an", "one") else int(m.group(1))
    unit = {"minute": timedelta(minutes=1), "hour": timedelta(hours=1), "day": timedelta(days=1),
            "week": timedelta(weeks=1), "month": timedelta(days=30)}[m.group(2)]
    return NOW - n * unit


def parse_date(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        v = value / 1000 if value > 10**11 else value
        return datetime.fromtimestamp(v, timezone.utc)
    s = str(value).strip()
    if s.isdigit():
        return parse_date(int(s))
    try:
        return email.utils.parsedate_to_datetime(s).astimezone(timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

_last_hit = {}


def fetch(url, accept="*/*"):
    host = urllib.parse.urlparse(url).netloc
    wait = 1.0 - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)  # be polite: at most ~1 request per second per host
    req = urllib.request.Request(url, headers={"User-Agent": FEED_CFG["user_agent"], "Accept": accept})
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _last_hit[host] = time.time()
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (400, 401, 403, 404):
                break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last_err}")


def fetch_xml(url, tries=3):
    """Some hosts occasionally answer with a bot-check HTML page; retry, then give up."""
    for attempt in range(tries):
        body = fetch(url)
        try:
            return ET.fromstring(body.encode("utf-8"))
        except ET.ParseError:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{url}: did not return valid XML after {tries} tries")


def fetch_json(url):
    return json.loads(fetch(url, accept="application/json"))


def job(title, org, location, posted, url, source, text="", mission=False):
    return {"title": strip_tags(title), "org": strip_tags(org), "location": strip_tags(location),
            "posted": posted, "url": url, "source": source, "text": text or "", "mission": mission}


# --------------------------------------------------------------------------- #
# Sources. Each returns a list of raw jobs (before role/location filters).
# --------------------------------------------------------------------------- #

IDEALIST_HIT = re.compile(r'id="search-hit-[0-9a-f]+"')


def src_idealist(cfg):
    out = []
    slugs = [(s, True) for s in cfg["nyc_pages"]] + [(s, False) for s in cfg["national_pages"]]
    for slug, is_nyc_page in slugs:
        seen_ids = set()
        for page in range(1, cfg.get("pages_per_slug", 1) + 1):
            url = f"https://www.idealist.org/en/{slug}" + (f"?page={page}" if page > 1 else "")
            body = fetch(url)
            ids = re.findall(r'id="search-hit-([0-9a-f]+)"', body)
            if not ids or ids[0] in seen_ids:  # Idealist currently ignores ?page= for crawlers
                break
            seen_ids.update(ids)
            blocks = IDEALIST_HIT.split(body)[1:]
            for b in blocks:
                b = b[:12000]
                href = re.search(r'href="(/en/[^"]+)"', b)
                title = re.search(r'data-qa-id="search-result-link"[^>]*>([^<]+)<', b)
                org = re.search(r"<h4[^>]*>(?:<[^>]+>)*([^<]+)<", b)
                if not (href and title):
                    continue
                tags = [html.unescape(t).strip() for t in re.findall(r"<span[^>]*>([^<]+)</span></span>", b)]
                posted = re.search(r">\s*(Posted [^<]+)<", b)
                loc_tags = [t for t in tags if not re.search(r"full time|part time|temporary|contract|\$|usd|/ ?(year|hour)", t, re.I)]
                location = ", ".join(loc_tags)
                if is_nyc_page and not NYC.search(location.lower()) and "remote" not in location.lower():
                    location = (location + ", " if location else "") + "New York, NY"
                out.append(job(title.group(1), org.group(1) if org else "", location,
                               parse_relative(posted.group(1)) if posted else None,
                               "https://www.idealist.org" + href.group(1), "Idealist", mission=True))
    if not out:
        raise RuntimeError("Idealist returned no listings (page layout may have changed)")
    return out


def src_higheredjobs(cfg):
    out = []
    for cat in cfg["categories"]:
        root = fetch_xml(f"https://www.higheredjobs.com/rss/categoryFeed.cfm?catID={cat}")
        for it in root.iter("item"):
            desc = html.unescape(it.findtext("description") or "")
            m = re.match(r"^(.*)\(([^()]*)\)\s*$", desc)
            org, loc = (m.group(1).strip(), m.group(2).strip()) if m else (desc, "")
            out.append(job(it.findtext("title"), org, loc, parse_date(it.findtext("pubDate")),
                           it.findtext("link"), "HigherEdJobs"))
    return out


def src_himalayas(cfg):
    out = []
    for q in cfg["queries"]:
        data = fetch_json("https://himalayas.app/jobs/api/search?q=" + urllib.parse.quote(q))
        for j in data.get("jobs", []):
            exp = parse_date(j.get("expiryDate"))
            if exp and exp < NOW:
                continue
            r = j.get("locationRestrictions") or []
            us_in = [c for c in r if US.search(c.lower())]
            if not r:
                where = "Remote (anywhere)"
            elif us_in and len(r) > 1:
                where = f"Remote ({us_in[0]} + {len(r) - 1} other countries)"
            else:
                where = "Remote (" + ", ".join(r[:4]) + ("..." if len(r) > 4 else "") + ")"
            o = job(j.get("title"), j.get("companyName"), where,
                    parse_date(j.get("pubDate")), j.get("applicationLink") or j.get("guid"), "Himalayas",
                    text=strip_tags(j.get("excerpt", "")))
            o["restrictions"] = r
            out.append(o)
    return out


def src_remotive(cfg):
    out = []
    for q in cfg["queries"]:
        data = fetch_json("https://remotive.com/api/remote-jobs?search=" + urllib.parse.quote(q))
        for j in data.get("jobs", []):
            loc = j.get("candidate_required_location") or "Anywhere"
            o = job(j.get("title"), j.get("company_name"), "Remote (" + loc + ")", parse_date(j.get("publication_date")),
                    j.get("url"), "Remotive", text=strip_tags(j.get("description", ""))[:2000])
            o["restrictions"] = [loc]
            out.append(o)
    return out


def src_remoteok(cfg):
    out = []
    for tag in cfg["tags"]:
        data = fetch_json("https://remoteok.com/api?tag=" + urllib.parse.quote(tag))
        for j in data[1:] if isinstance(data, list) else []:  # item 0 is Remote OK's legal notice
            loc = j.get("location") or "Anywhere"
            o = job(j.get("position"), j.get("company"), "Remote (" + loc + ")", parse_date(j.get("date")),
                    j.get("url"), "Remote OK", text=strip_tags(j.get("description", ""))[:2000])
            o["restrictions"] = [loc]
            out.append(o)
    return out


def src_greenhouse(cfg):
    out = []
    for slug, name in cfg["boards"].items():
        data = fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
        for j in data.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            out.append(job(j.get("title"), name, loc, parse_date(j.get("first_published") or j.get("updated_at")),
                           j.get("absolute_url"), "Greenhouse", mission=True))
    return out


def src_lever(cfg):
    out = []
    for slug, name in cfg["boards"].items():
        data = fetch_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            loc = cats.get("location") or ""
            wt = (j.get("workplaceType") or "").lower()
            if wt in ("remote", "hybrid") and wt not in loc.lower():
                loc = f"{loc} ({wt})" if loc else wt.title()
            if j.get("country") == "US" and loc and not US.search(loc.lower()):
                loc += ", US"
            out.append(job(j.get("text"), name, loc, parse_date(j.get("createdAt")),
                           j.get("hostedUrl"), "Lever", mission=True))
    return out


def src_bamboohr(cfg):
    out = []
    for sub, name in cfg["boards"].items():
        data = fetch_json(f"https://{sub}.bamboohr.com/careers/list")
        for j in data.get("result", []):
            loc = j.get("location") or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("state")) if x)
            if j.get("isRemote"):
                where = (where + " (remote)").strip()
            out.append(job(j.get("jobOpeningName"), name, where, None,  # BambooHR gives no posting date
                           f"https://{sub}.bamboohr.com/careers/{j.get('id')}", "BambooHR", mission=True))
    return out


SOURCES = {
    "idealist": ("Idealist", src_idealist),
    "higheredjobs": ("HigherEdJobs", src_higheredjobs),
    "himalayas": ("Himalayas", src_himalayas),
    "remotive": ("Remotive", src_remotive),
    "remoteok": ("Remote OK", src_remoteok),
    "greenhouse": ("Greenhouse", src_greenhouse),
    "lever": ("Lever", src_lever),
    "bamboohr": ("BambooHR", src_bamboohr),
}


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"seen": {}, "items": []}


def collect(state):
    report, kept = [], []
    cutoff = NOW - timedelta(days=FEED_CFG["max_age_days"])
    for key, (label, fn) in SOURCES.items():
        cfg = SRC_CFG.get(key, {})
        if not cfg.get("enabled"):
            continue
        try:
            raw = fn(cfg)
            error = None
        except Exception as e:  # keep last run's items for a source that is down
            raw, error = None, str(e)[:200]
        if raw is None:
            carried = [i for i in state.get("items", []) if i["source"] == label]
            for i in carried:
                i["posted"] = parse_date(i["posted"])
                i["text"] = ""
            kept.extend(carried)
            report.append((label, "ERROR", 0, 0, len(carried), error))
            continue
        roles = [j for j in raw if j["title"] and j["url"] and role_ok(j["title"])]
        n_loc = 0
        for j in roles:
            if j["org"].lower() in EXCLUDE_ORGS:
                continue
            j["where"] = classify_location(j["location"], restrictions=j.get("restrictions"))
            t = j["title"].lower()
            if j["where"] == "Remote (US)" and NON_US.search(t) and not US.search(t):
                j["where"] = None  # e.g. "Video Producer, London" on a remote board
            if not j["where"]:
                continue
            n_loc += 1
            if j["posted"] and j["posted"] < cutoff:
                continue
            kept.append(j)
        report.append((label, "ok", len(raw), len(roles), n_loc, None))
    return kept, report


def day(d):
    """Round to noon UTC on the same day so dates stay stable between runs."""
    return d.replace(hour=12, minute=0, second=0, microsecond=0) if d else None


def merge(jobs, state):
    seen = state.get("seen", {})
    by_key = {}
    for j in jobs:
        k = dedupe_key(j["title"], j["org"])
        j["key"] = k
        cur = by_key.get(k)
        if cur is None:
            j["also"] = []
            by_key[k] = j
            continue
        a, b = (cur, j) if SOURCE_PRIORITY.get(cur["source"], 0) >= SOURCE_PRIORITY.get(j["source"], 0) else (j, cur)
        a["also"] = sorted(set(cur.get("also", []) + j.get("also", []) + [b["source"]]) - {a["source"]})
        a["mission"] = a["mission"] or b["mission"]
        a["posted"] = min([d for d in (a["posted"], b["posted"]) if d], default=None)
        by_key[k] = a
    items = []
    for k, j in by_key.items():
        rec = seen.get(k)
        if rec is None:  # first time we see this job: freeze its dates
            posted = day(min(j["posted"], NOW)) if j["posted"] else None
            rec = {"first_seen": day(NOW).isoformat(), "posted": posted.isoformat() if posted else None}
            seen[k] = rec
        j["dated"] = rec["posted"] is not None
        j["date"] = parse_date(rec["posted"] or rec["first_seen"])
        blob = " ".join([j["org"], j["text"], j["title"]]).lower()
        j["preferred"] = j["mission"] or bool(PREFERRED.search(blob))
        items.append(j)
    if FEED_CFG.get("remote_only_if_preferred"):
        items = [i for i in items if i["preferred"] or i["where"] != "Remote (US)"]
    items.sort(key=lambda i: (i["date"], i["preferred"], i["title"].lower()), reverse=True)
    items = items[: FEED_CFG["max_items"]]
    horizon = NOW - timedelta(days=90)  # forget old keys so the state file stays small
    live = {i["key"] for i in items}
    state["seen"] = {k: v for k, v in seen.items() if k in live or parse_date(v["first_seen"]) > horizon}
    return items


def build_rss(items):
    esc = html.escape
    newest = max((i["date"] for i in items), default=NOW)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
           "<channel>",
           f"<title>{esc(FEED_CFG['title'])}</title>",
           f"<link>{esc(FEED_CFG['link'])}</link>",
           f"<description>{esc(FEED_CFG['description'])}</description>",
           f'<atom:link href="{esc(FEED_CFG["self_url"])}" rel="self" type="application/rss+xml"/>',
           "<language>en-us</language>",
           f"<lastBuildDate>{email.utils.format_datetime(newest)}</lastBuildDate>",
           "<ttl>360</ttl>"]
    for i in items:
        star = "★ " if i["preferred"] else ""
        title = f"{star}{i['title']} | {i['org'] or 'Unknown org'} | {i['where']}"
        via = i["source"] + (" (also on " + ", ".join(i["also"]) + ")" if i["also"] else "")
        body = (f"<p><b>{esc(i['title'])}</b><br/>"
                f"<b>Organization:</b> {esc(i['org'] or 'Not listed')}<br/>"
                f"<b>Location:</b> {esc(i['location'] or i['where'])}<br/>"
                f"<b>Posted:</b> {i['date'].strftime('%b %d, %Y')}"
                f"{'' if i['dated'] else ' (first seen; source gives no date)'}<br/>"
                f"<b>Source:</b> {esc(via)}"
                f"{'<br/><b>Preferred:</b> mission-driven employer' if i['preferred'] else ''}</p>"
                f'<p><a href="{esc(i["url"])}">View the listing</a></p>')
        guid = hashlib.sha1(i["key"].encode()).hexdigest()[:20]
        out += ["<item>",
                f"<title>{esc(title)}</title>",
                f"<link>{esc(i['url'])}</link>",
                f'<guid isPermaLink="false">{guid}</guid>',
                f"<pubDate>{email.utils.format_datetime(i['date'])}</pubDate>",
                f"<category>{'Preferred' if i['preferred'] else 'Other'}</category>",
                f"<category>{esc(i['source'])}</category>",
                f"<description>{esc(body)}</description>",
                "</item>"]
    out += ["</channel>", "</rss>", ""]
    return "\n".join(out)


def main():
    dry = "--dry-run" in sys.argv
    state = load_state()
    jobs, report = collect(state)
    items = merge(jobs, state)

    print(f"{'source':<14}{'status':<8}{'fetched':>8}{'role':>6}{'in-area':>9}")
    for label, status, n, r, loc, err in report:
        print(f"{label:<14}{status:<8}{n:>8}{r:>6}{loc:>9}" + (f"   {err}" if err else ""))
    print(f"\nFeed items after age filter + dedupe: {len(items)} "
          f"({sum(i['preferred'] for i in items)} preferred, "
          f"{sum(bool(i['also']) for i in items)} merged duplicates)")

    if dry:
        for i in items[:25]:
            print(f"  {'*' if i['preferred'] else ' '} {i['date']:%Y-%m-%d}  {i['title'][:60]:<60} | {i['org'][:30]:<30} | {i['where']} [{i['source']}]")
        return

    if not items and any(r[1] == "ERROR" for r in report):
        print("Every source failed or returned nothing; leaving the old feed in place.")
        return

    xml = build_rss(items)
    ET.fromstring(xml.encode("utf-8"))  # fail loudly if the XML is malformed
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not FEED_PATH.exists() or FEED_PATH.read_text(encoding="utf-8") != xml:
        FEED_PATH.write_text(xml, encoding="utf-8")
        print("docs/feed.xml updated")
    else:
        print("docs/feed.xml unchanged")

    state["items"] = [{**{k: i[k] for k in ("title", "org", "location", "url", "source", "mission", "where")},
                       "posted": i["date"].isoformat() if i["dated"] else None}
                      for i in items]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
