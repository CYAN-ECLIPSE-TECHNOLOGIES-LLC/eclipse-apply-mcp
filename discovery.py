#!/usr/bin/env python3
# Eclipse Apply MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Eclipse Apply — job discovery adapters.

"""Eclipse Apply — job discovery adapters.

Reads the PUBLIC, unauthenticated JSON feeds that power company careers
pages (Greenhouse, Lever, Ashby, Workable). No keys, no logins, no scraping
of HTML. Discovery only — this module never submits anything anywhere.

Each adapter takes (board_slug, fetcher) where fetcher(url) -> parsed JSON,
so tests can inject canned payloads and the server can inject urllib.
Normalized record:
  {job_id, source, board, company, title, location, remote,
   comp_min, comp_max, comp_currency, url, posted_date, description_text}
Comp fields are None when the employer did not publish compensation —
we never guess a salary.
"""
import html
import re
import urllib.request

UA = "EclipseApply/0.1 (+https://cetech.ai; discovery of public job feeds)"


def http_fetcher(url):
    import json
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        # Fallback: some egress paths truncate large chunked bodies for
        # urllib; curl handles them. Board payloads run to several MB.
        import subprocess
        out = subprocess.run(
            ["curl", "-s", "--fail", "--max-time", "60", "-A", UA,
             "-H", "Accept: application/json", url],
            capture_output=True, timeout=75)
        if out.returncode != 0:
            raise RuntimeError(f"fetch failed for {url}: "
                               f"{out.stderr.decode('utf-8', 'replace')[:150]}")
        return json.loads(out.stdout.decode("utf-8", "replace"))


def strip_html(text):
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _remote(location, title=""):
    blob = f"{location or ''} {title or ''}".lower()
    return "remote" in blob


def _company_name(slug):
    return slug.replace("-", " ").replace("_", " ").title()


def _base(source, board, ext_id, company=None):
    return {"job_id": f"{source}:{board}:{ext_id}", "source": source,
            "board": board, "company": company or _company_name(board)}


# ------------------------------------------------------------------ Greenhouse
def greenhouse(board, fetcher=http_fetcher):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    data = fetcher(url)
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        rec = _base("greenhouse", board, j.get("id"))
        rec.update({
            "title": j.get("title", ""),
            "location": loc,
            "remote": _remote(loc, j.get("title", "")),
            "comp_min": None, "comp_max": None, "comp_currency": None,
            "url": j.get("absolute_url", ""),
            "posted_date": (j.get("updated_at") or "")[:10] or None,
            "description_text": strip_html(j.get("content", ""))[:6000],
        })
        out.append(rec)
    return out


# ------------------------------------------------------------------ Lever
def lever(board, fetcher=http_fetcher):
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    data = fetcher(url)
    out = []
    for j in data if isinstance(data, list) else []:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        sr = j.get("salaryRange") or {}
        created = j.get("createdAt")
        posted = None
        if created:
            import datetime
            posted = datetime.datetime.fromtimestamp(
                created / 1000, datetime.timezone.utc).strftime("%Y-%m-%d")
        rec = _base("lever", board, j.get("id"))
        rec.update({
            "title": j.get("text", ""),
            "location": loc,
            "remote": _remote(loc, j.get("text", "")),
            "comp_min": sr.get("min"), "comp_max": sr.get("max"),
            "comp_currency": sr.get("currency"),
            "url": j.get("hostedUrl", "") or j.get("applyUrl", ""),
            "posted_date": posted,
            "description_text": strip_html(
                j.get("descriptionPlain") or j.get("description", ""))[:6000],
        })
        out.append(rec)
    return out


# ------------------------------------------------------------------ Ashby
def ashby(board, fetcher=http_fetcher):
    url = (f"https://api.ashbyhq.com/posting-api/job-board/{board}"
           f"?includeCompensation=true")
    data = fetcher(url)
    out = []
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        if not isinstance(comp, dict):
            comp = {}
        # Real Ashby shape: scrapeableCompensationSalarySummary is a
        # STRING like "$211.4K – $290.6K • Offers Equity" (some boards may
        # vary) — parse it; never invent numbers that are not printed.
        summary = comp.get("scrapeableCompensationSalarySummary")
        cmin = cmax = ccur = None
        if isinstance(summary, dict):
            cmin, cmax = summary.get("minValue"), summary.get("maxValue")
            ccur = summary.get("currencyCode")
        elif isinstance(summary, str):
            m = re.search(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?\s*[–—-]\s*"
                          r"\$?\s*([\d,]+(?:\.\d+)?)\s*([Kk])?", summary)
            if m:
                def _amt(num, k):
                    v = float(num.replace(",", ""))
                    return int(v * 1000) if k else int(v)
                cmin = _amt(m.group(1), m.group(2))
                cmax = _amt(m.group(3), m.group(4))
                ccur = "USD" if "$" in summary else None
        loc = j.get("location") or ""
        rec = _base("ashby", board, j.get("id"))
        rec.update({
            "title": j.get("title", ""),
            "location": loc,
            "remote": bool(j.get("isRemote")) or _remote(loc, j.get("title", "")),
            "comp_min": cmin, "comp_max": cmax, "comp_currency": ccur,
            "url": j.get("jobUrl", "") or j.get("applyUrl", ""),
            "posted_date": (j.get("publishedAt") or "")[:10] or None,
            "description_text": (j.get("descriptionPlain") or
                                 strip_html(j.get("descriptionHtml", "")))[:6000],
        })
        out.append(rec)
    return out


# ------------------------------------------------------------------ Workable
def workable(board, fetcher=http_fetcher):
    url = f"https://apply.workable.com/api/v1/widget/accounts/{board}"
    data = fetcher(url)
    jobs = data.get("jobs", []) if isinstance(data, dict) else []
    company = (data.get("name") if isinstance(data, dict) else None)
    # Multi-location postings SHARE one shortcode (live-verified EB-03,
    # 2026-10-26: Epignosis ran 12 postings on 10 shortcodes — the same
    # role in Athens and Heraklion). Keying job_id on the shortcode
    # alone overwrites locations in the cache, so duplicates get a
    # city suffix; single-location boards keep the plain shortcode ID.
    from collections import Counter
    codes = Counter(j.get("shortcode") or j.get("id") for j in jobs)
    seen = {}
    out = []
    for j in jobs:
        loc_parts = [j.get("city", ""), j.get("country", "")]
        loc = ", ".join(p for p in loc_parts if p)
        code = j.get("shortcode") or j.get("id")
        ext_id = code
        if codes.get(code, 0) > 1:
            slug = re.sub(r"[^a-z0-9]+", "-",
                          (j.get("city") or j.get("state") or "")
                          .lower()).strip("-")
            ext_id = f"{code}-{slug}" if slug else code
            n = seen.get(ext_id, 0)
            seen[ext_id] = n + 1
            if n:
                ext_id = f"{ext_id}-{n + 1}"
        rec = _base("workable", board, ext_id, company=company)
        rec.update({
            "title": j.get("title", ""),
            "location": loc,
            # Real widget payloads name the remote flag "telecommuting";
            # "telework" is kept for older/canned payloads. Checking only
            # "telework" silently marked every real remote job on-site
            # (found by the EB-03 live-board smoke, 2026-10-26).
            "remote": bool(j.get("telework") or j.get("telecommuting"))
                      or _remote(loc, j.get("title", "")),
            "comp_min": None, "comp_max": None, "comp_currency": None,
            "url": j.get("url", ""),
            "posted_date": (j.get("created_at") or "")[:10] or None,
            "description_text": strip_html(j.get("description", ""))[:6000],
        })
        out.append(rec)
    return out


ADAPTERS = {"greenhouse": greenhouse, "lever": lever,
            "ashby": ashby, "workable": workable}


def discover(source, board, fetcher=http_fetcher):
    """Pull one board and return normalized job records."""
    fn = ADAPTERS.get(source)
    if not fn:
        raise ValueError(f"unknown source '{source}' "
                         f"(have: {', '.join(sorted(ADAPTERS))})")
    return fn(board, fetcher)

# Eclipse Apply MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
