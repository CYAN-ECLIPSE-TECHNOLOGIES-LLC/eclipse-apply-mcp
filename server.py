#!/usr/bin/env python3
"""Eclipse Apply MCP — v0.1.0.

Powered by Cyan Eclipse Technologies (cetech.ai).

The auto-apply tool that shows its work. The Agent Wallet pattern applied
to job hunting — POLICY-SCOPED AUTONOMY:

  * The human grants a written policy: target roles, salary floor,
    locations/remote, company allowlist + blocklist, daily cap, approval
    threshold, expiry, pause/kill switch. NOTHING is prepared or submitted
    outside that grant; refusals are first-class outcomes with reasons.
  * Review-first: matched jobs land as PREPARED applications. A human
    approves before anything moves.
  * Truthfulness is a hard, machine-checked rule. Tailoring re-orders and
    re-emphasizes REAL resume entries only. Every tailored resume ships
    with a verifiable diff: zero added facts, or it does not ship.
  * Every application, approval and refusal emits a RECEIPT into an
    append-only, hash-chained ledger.

v0.1 SUBMISSION IS DRY-RUN ONLY. The Greenhouse submission adapter builds
and validates the exact payload an application WOULD send, records it in
the receipt, and TRANSMITS NOTHING to any employer. There is deliberately
no code path in this file that posts an application. Live transmission is
a future, separately-gated feature (see README: live-submit unlock
criteria).

Transport: MCP over stdio (newline-delimited JSON-RPC 2.0), stdlib only.
State file: $ECLIPSE_APPLY_STATE or ./state.json next to this file.
"""
import difflib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import discovery  # noqa: E402

STATE_PATH = Path(os.environ.get(
    "ECLIPSE_APPLY_STATE", Path(__file__).with_name("state.json")))
SUBMISSION_MODE = "DRY-RUN"  # v0.1: payloads are built + recorded, NEVER sent

STOPWORDS = set("""the a an and or of to in for with on at by from as is are was
were be been you your we our they their this that will who what have has had
not but all any can had her was one our out day get has him his how man new
now old see two way work team years experience including etc about into
using used use build building built develop developed development software
engineer engineering role job requirements qualifications plus strong""".split())


class ToolError(Exception):
    pass


def now() -> int:
    return int(time.time())


def today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


# --------------------------------------------------------------------- state
def empty_state() -> dict:
    return {"profile": None, "policy": None, "paused": False,
            "jobs": {}, "applications": {}, "receipts": []}


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return empty_state()


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=1))


# ------------------------------------------------------------------ receipts
def emit_receipt(state: dict, rtype: str, detail: dict) -> dict:
    """Append-only, hash-chained. Every receipt states the submission mode."""
    prev = state["receipts"][-1]["hash"] if state["receipts"] else "GENESIS"
    entry = {"seq": len(state["receipts"]) + 1, "ts": now(), "type": rtype,
             "mode": SUBMISSION_MODE, "prev_hash": prev, "detail": detail}
    entry["hash"] = sha(prev + canon({k: v for k, v in entry.items()
                                      if k != "hash"}))
    state["receipts"].append(entry)
    return entry


def verify_receipt_chain(receipts) -> bool:
    prev = "GENESIS"
    for e in receipts:
        if e.get("prev_hash") != prev:
            return False
        expect = sha(prev + canon({k: v for k, v in e.items() if k != "hash"}))
        if e.get("hash") != expect:
            return False
        prev = e["hash"]
    return True


# -------------------------------------------------------------- resume ingest
def parse_resume(text: str) -> dict:
    """Structure a plain-text/markdown resume AS WRITTEN.

    The parser may structure; it never embellishes: every skill, title,
    company and bullet in the profile is a verbatim slice of the source.
    """
    lines = [ln.rstrip() for ln in text.splitlines()]
    name, headline = "", ""
    contact = [ln.strip() for ln in lines
               if "@" in ln and " " not in ln.strip()]
    skills, experience, education = [], [], []
    section, current = None, None
    for ln in lines:
        s = ln.strip()
        if s.startswith("# ") and not name:
            name = s[2:].strip()
            continue
        if s.startswith("## "):
            section = s[3:].strip().lower()
            current = None
            continue
        if s.startswith("### "):
            head = s[4:].strip()
            m = re.match(r"^(.*?)\s*[—–-]\s*(.*?)\s*\((.*?)\)\s*$", head)
            if m:
                current = {"title": m.group(1).strip(),
                           "company": m.group(2).strip(),
                           "dates": m.group(3).strip(), "bullets": []}
            else:
                current = {"title": head, "company": "", "dates": "",
                           "bullets": []}
            experience.append(current)
            continue
        if not s:
            continue
        if section == "skills" or (section is None and not experience
                                  and "," in s and not headline and "@" not in s
                                  and s.lower() not in (name.lower(),)):
            if section == "skills":
                items = re.split(r"[,\n]", s.lstrip("- ").strip())
                skills.extend(i.strip() for i in items if i.strip())
            continue
        if section == "experience" and current is not None:
            if s.startswith("- ") or s.startswith("* "):
                current["bullets"].append(s[2:].strip())
            continue
        if section == "education":
            if s.startswith("- ") or s.startswith("* "):
                education.append(s[2:].strip())
            continue
        if section is None and not headline and s != name and "@" not in s \
                and "SYNTHETIC" not in s and len(s) <= 80:
            headline = s
    source_hash = sha(text)
    return {"name": name, "headline": headline,
            "contact": contact[0] if contact else "",
            "skills": skills, "experience": experience,
            "education": education, "raw_text": text,
            "source_hash": source_hash,
            "version": "sha256:" + source_hash[:12], "ingested_at": now()}


def public_profile(profile: dict) -> dict:
    return {k: v for k, v in profile.items() if k != "raw_text"}


# -------------------------------------------------------------------- policy
def make_policy(args: dict) -> dict:
    pol = {
        "target_roles": [r.strip() for r in args.get("target_roles", []) if r.strip()],
        "salary_floor": args.get("salary_floor"),
        "locations": [l.strip() for l in args.get("locations", []) if l.strip()],
        "remote_ok": bool(args.get("remote_ok", True)),
        "allowlist": [c.strip().lower() for c in args.get("allowlist", []) if c.strip()],
        "blocklist": [c.strip().lower() for c in args.get("blocklist", []) if c.strip()],
        "daily_cap": int(args.get("daily_cap", 5)),
        "approval_threshold": int(args.get("approval_threshold", 60)),
        "granted_at": now(),
        "expires_at": (now() + int(args["expires_in_days"]) * 86400)
                      if args.get("expires_in_days") else None,
    }
    pol["snapshot_id"] = "pol_" + sha(canon(pol))[:12]
    return pol


def submissions_today(state: dict) -> int:
    return sum(1 for a in state["applications"].values()
               if a.get("submitted_day") == today())


def check_policy(state: dict, job: dict, match: dict = None):
    """Policy FIRST. Returns (allowed: bool, reasons: [str]).

    Every refusal names the clause that refused. No silent drops.
    """
    reasons = []
    if state.get("paused"):
        reasons.append("PAUSED: kill switch engaged — no applications "
                       "are prepared or submitted while paused")
        return False, reasons
    pol = state.get("policy")
    if not pol:
        return False, ["NO POLICY: no written grant exists — the agent may "
                       "not apply without a policy"]
    if pol.get("expires_at") and now() > pol["expires_at"]:
        reasons.append("POLICY EXPIRED: the grant's expiry has passed; "
                       "the owner must re-grant")
    company = (job.get("company") or "").lower()
    if company in pol["blocklist"]:
        reasons.append(f"BLOCKLIST: {job.get('company')} is on the policy "
                       f"blocklist")
    if pol["allowlist"] and company not in pol["allowlist"]:
        reasons.append(f"ALLOWLIST: {job.get('company')} is not on the "
                       f"policy allowlist")
    floor = pol.get("salary_floor")
    if floor is not None:
        if job.get("comp_max") is not None and job["comp_max"] < floor:
            reasons.append(f"SALARY FLOOR: published comp tops at "
                           f"{job['comp_max']} < floor {floor}")
        elif job.get("comp_min") is not None and job["comp_min"] >= floor:
            pass  # range clears the floor
    loc = (job.get("location") or "").lower()
    if pol["locations"]:
        loc_ok = any(l.lower() in loc for l in pol["locations"])
        if job.get("remote") and pol.get("remote_ok"):
            loc_ok = True
        if not loc_ok:
            reasons.append(f"LOCATION: '{job.get('location')}' matches none "
                           f"of the policy locations {pol['locations']} "
                           f"(remote_ok={pol.get('remote_ok')})")
    if match is not None:
        roles_hit = match["signals"]["title_matched_roles"]
        if pol["target_roles"] and not roles_hit:
            reasons.append(f"ROLE: title '{job.get('title')}' matches none "
                           f"of the target roles {pol['target_roles']}")
        if match["score"] < pol["approval_threshold"]:
            reasons.append(f"APPROVAL THRESHOLD: match score "
                           f"{match['score']} < threshold "
                           f"{pol['approval_threshold']} — not worth the "
                           f"owner's review time")
    if submissions_today(state) >= pol["daily_cap"]:
        reasons.append(f"DAILY CAP: {pol['daily_cap']} submissions already "
                       f"made today")
    return (len(reasons) == 0), reasons


# ------------------------------------------------------------------ matching
def _keywords(text: str):
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]{2,}", (text or "").lower())
            if w not in STOPWORDS}


def match_job(profile: dict, job: dict, policy: dict = None) -> dict:
    """Transparent score 0-100. Every point is explained; no black box."""
    signals, rationale = {}, []
    # Title fit: 0-40
    title_words = _keywords(job.get("title", ""))
    roles = (policy or {}).get("target_roles") or [profile.get("headline", "")]
    matched_roles = [r for r in roles
                     if _keywords(r) & title_words or
                     r.lower() in (job.get("title") or "").lower()]
    title_pts = 40 if matched_roles else (15 if title_words &
                                          _keywords(profile.get("headline", ""))
                                          else 0)
    signals["title_fit"] = title_pts
    signals["title_matched_roles"] = matched_roles
    rationale.append(f"title fit {title_pts}/40: "
                     + (f"matches target role(s) {matched_roles}"
                        if matched_roles else "no target-role overlap"))
    # Skills overlap: 0-30, proportional to profile skills the job asks for
    job_words = _keywords(job.get("title", "") + " " +
                          job.get("description_text", ""))
    hit_skills = [s for s in profile.get("skills", [])
                  if s.lower() in job_words or
                  all(tok in job_words for tok in _keywords(s))]
    denom = max(4, min(10, len(profile.get("skills", []) or [4])))
    skills_pts = round(30 * min(1.0, len(hit_skills) / denom))
    signals["skills_overlap"] = skills_pts
    signals["skills_hit"] = hit_skills
    rationale.append(f"skills overlap {skills_pts}/30: job text names "
                     f"{len(hit_skills)} profile skills {hit_skills[:6]}")
    # Comp vs floor: 0-15 (unknown comp = half credit, stated honestly)
    floor = (policy or {}).get("salary_floor")
    if floor is None:
        comp_pts, note = 15, "no salary floor in policy"
    elif job.get("comp_max") is None:
        comp_pts, note = 7, "employer did not publish comp — cannot verify vs floor"
    elif job["comp_max"] >= floor:
        comp_pts, note = 15, f"published comp up to {job['comp_max']} >= floor {floor}"
    else:
        comp_pts, note = 0, f"published comp up to {job['comp_max']} < floor {floor}"
    signals["comp"] = comp_pts
    rationale.append(f"comp {comp_pts}/15: {note}")
    # Location: 0-15
    locs = (policy or {}).get("locations") or []
    if not locs:
        loc_pts, note = 15, "no location constraint in policy"
    elif job.get("remote") and (policy or {}).get("remote_ok", True):
        loc_pts, note = 15, "remote role, policy allows remote"
    elif any(l.lower() in (job.get("location") or "").lower() for l in locs):
        loc_pts, note = 15, f"location '{job.get('location')}' in policy locations"
    else:
        loc_pts, note = 0, f"location '{job.get('location')}' outside policy locations"
    signals["location"] = loc_pts
    rationale.append(f"location {loc_pts}/15: {note}")
    return {"score": title_pts + skills_pts + comp_pts + loc_pts,
            "signals": signals, "rationale": rationale}


# ----------------------------------------------------------------- tailoring
def tailor_resume(profile: dict, job: dict) -> dict:
    """Re-order / re-emphasize REAL entries only.

    Output is assembled exclusively from verbatim source lines (bullets,
    titles, companies, dates, skills). Nothing is reworded, nothing added.
    Returns the tailored text + a machine-checkable truthfulness proof.
    """
    job_words = _keywords(job.get("title", "") + " " +
                          job.get("description_text", ""))
    hit_skills = [s for s in profile["skills"]
                  if s.lower() in job_words]
    rest_skills = [s for s in profile["skills"] if s not in hit_skills]
    ordered_skills = hit_skills + rest_skills

    def entry_score(entry):
        blob = " ".join(entry["bullets"]).lower()
        return sum(1 for w in job_words if w in blob)

    entries = sorted(profile["experience"],
                     key=lambda e: -entry_score(e))
    lines = [f"# {profile['name']}", profile.get("headline", ""),
             profile.get("contact", ""), "", "## Skills",
             ", ".join(ordered_skills), "", "## Experience", ""]
    for e in entries:
        head = f"### {e['title']} — {e['company']}"
        if e.get("dates"):
            head += f" ({e['dates']})"
        lines.append(head)
        scored = sorted(e["bullets"],
                        key=lambda b: -sum(1 for w in job_words
                                           if w in b.lower()))
        for b in scored:
            lines.append(f"- {b}")
        lines.append("")
    if profile.get("education"):
        lines.append("## Education")
        for ed in profile["education"]:
            lines.append(f"- {ed}")
    tailored = "\n".join(lines).strip() + "\n"
    diff = "\n".join(difflib.unified_diff(
        profile["raw_text"].splitlines(), tailored.splitlines(),
        fromfile="resume.source", tofile="resume.tailored", lineterm=""))
    proof = verify_truthful(profile, tailored, ordered_skills)
    return {"tailored_text": tailored, "diff": diff,
            "diff_hash": "sha256:" + sha(diff)[:16], "proof": proof,
            "skills_emphasized": hit_skills}


def verify_truthful(profile: dict, tailored_text: str,
                    tailored_skills=None) -> dict:
    """Machine-checkable: the tailored resume adds ZERO new facts.

    Checks: (1) every experience bullet is verbatim from the source;
    (2) every (title, company, dates) heading exists in the source;
    (3) the tailored skill set equals the source skill set (order may
    differ); (4) the candidate name matches. Any violation is listed.
    """
    violations = []
    src_bullets = {b for e in profile["experience"] for b in e["bullets"]}
    src_heads = {(e["title"], e["company"], e["dates"])
                 for e in profile["experience"]}
    t = parse_resume(tailored_text)
    for e in t["experience"]:
        for b in e["bullets"]:
            if b not in src_bullets:
                violations.append(f"added bullet not in source: {b!r}")
        if (e["title"], e["company"], e["dates"]) not in src_heads:
            violations.append(f"added/altered role heading: {e['title']} / "
                                f"{e['company']} / {e['dates']}")
    if tailored_skills is not None and set(tailored_skills) != set(profile["skills"]):
        violations.append("skill set differs from source")
    if set(t["skills"]) != set(profile["skills"]):
        violations.append("parsed tailored skills differ from source skills")
    if t["name"] != profile["name"]:
        violations.append("candidate name differs from source")
    return {"truthful": not violations, "violations": violations,
            "rule": "tailoring = re-order/emphasis of verbatim source "
                    "facts only; zero added facts"}


# ------------------------------------------------------- Greenhouse dry-run
def build_greenhouse_payload(state: dict, app: dict) -> dict:
    """Construct + validate the payload a Greenhouse application WOULD
    carry. DRY-RUN: this dict is recorded in the receipt and RETURNED to
    the caller. It is never transmitted — v0.1 has no transmit code path.
    """
    profile, job = state["profile"], app["job"]
    payload = {
        "endpoint_shape": "POST https://boards.greenhouse.io/v1/boards/"
                          f"{job['board']}/jobs/{job['job_id'].split(':')[-1]}",
        "fields": {
            "first_name": profile["name"].split()[0],
            "last_name": " ".join(profile["name"].split()[1:]),
            "email": profile.get("contact", ""),
            "resume_text": app["tailored_text"],
            "job_id": job["job_id"].split(":")[-1],
            "source": "Eclipse Apply (DRY-RUN — not transmitted)",
        },
        "attachments": ["resume (tailored, diff-verified)"],
    }
    problems = []
    if not payload["fields"]["email"]:
        problems.append("profile has no contact email")
    if not payload["fields"]["resume_text"].strip():
        problems.append("tailored resume is empty")
    if not app["truthfulness"]["truthful"]:
        problems.append("truthfulness proof FAILED — payload must not ship")
    payload["validation"] = {"valid": not problems, "problems": problems,
                             "mode": SUBMISSION_MODE,
                             "transmitted": False}
    return payload


# --------------------------------------------------------------------- tools
def _need_profile(state):
    if not state.get("profile"):
        raise ToolError("no resume ingested yet — call ingest_resume first")
    return state["profile"]


def t_ingest_resume(state, args):
    text = args.get("resume_text") or ""
    if args.get("resume_path"):
        text = Path(args["resume_path"]).read_text()
    if not text.strip():
        raise ToolError("resume_text (or resume_path) is required")
    profile = parse_resume(text)
    state["profile"] = profile
    emit_receipt(state, "resume_ingested",
                 {"name": profile["name"], "version": profile["version"],
                  "skills": len(profile["skills"]),
                  "roles": len(profile["experience"]),
                  "note": "structured as written; nothing embellished"})
    return {"profile": public_profile(profile),
            "note": "Profile structured verbatim from the source resume."}


def t_set_policy(state, args):
    pol = make_policy(args)
    state["policy"] = pol
    receipt_text = (
        f"Application grant: roles {pol['target_roles'] or 'any'}, salary "
        f"floor {pol['salary_floor']}, locations {pol['locations'] or 'any'} "
        f"(remote_ok={pol['remote_ok']}), allowlist "
        f"{pol['allowlist'] or 'open'}, blocklist {pol['blocklist'] or 'none'}, "
        f"max {pol['daily_cap']} submissions/day, human review for every "
        f"application, matches below score {pol['approval_threshold']} are "
        f"refused outright, expiry {pol['expires_at']}. Nothing outside "
        f"this scope is authorized. Submission mode in v0.1: DRY-RUN.")
    emit_receipt(state, "policy_granted",
                 {"policy": pol, "grant_text": receipt_text})
    return {"policy": pol, "grant_text": receipt_text}


def t_get_policy(state, args):
    return {"policy": state.get("policy"), "paused": state.get("paused", False),
            "submissions_today": submissions_today(state),
            "submission_mode": SUBMISSION_MODE}


def t_search_jobs(state, args):
    boards = args.get("boards") or []
    if not boards:
        raise ToolError("boards is required: [{source, board}, ...]")
    found, errors = [], []
    for b in boards:
        try:
            jobs = discovery.discover(b["source"], b["board"])
            for j in jobs:
                state["jobs"][j["job_id"]] = j
            found.append({"source": b["source"], "board": b["board"],
                          "jobs": len(jobs)})
        except Exception as e:  # one bad board must not sink the search
            errors.append({"source": b.get("source"), "board": b.get("board"),
                           "error": str(e)[:200]})
    query = (args.get("query") or "").lower()
    cached = list(state["jobs"].values())
    if query:
        cached = [j for j in cached
                  if query in (j["title"] + " " + j["company"]).lower()]
    limit = int(args.get("limit", 25))
    return {"discovered": found, "errors": errors,
            "jobs": [{k: v for k, v in j.items() if k != "description_text"}
                     for j in cached[:limit]],
            "total_cached": len(state["jobs"])}


def t_match_jobs(state, args):
    profile = _need_profile(state)
    pol = state.get("policy")
    results = []
    for job in state["jobs"].values():
        m = match_job(profile, job, pol)
        allowed, reasons = check_policy(state, job, m)
        results.append({"job_id": job["job_id"], "title": job["title"],
                        "company": job["company"], "location": job["location"],
                        "url": job["url"], "score": m["score"],
                        "rationale": m["rationale"],
                        "policy_eligible": allowed,
                        "policy_reasons": reasons})
    results.sort(key=lambda r: -r["score"])
    return {"matches": results[:int(args.get("limit", 25))],
            "total": len(results), "submission_mode": SUBMISSION_MODE}


def t_prepare_application(state, args):
    profile = _need_profile(state)
    job = state["jobs"].get(args.get("job_id", ""))
    if not job:
        raise ToolError(f"unknown job_id {args.get('job_id')} — run "
                        f"search_jobs first")
    m = match_job(profile, job, state.get("policy"))
    allowed, reasons = check_policy(state, job, m)
    if not allowed:
        r = emit_receipt(state, "application_refused",
                         {"job_id": job["job_id"], "title": job["title"],
                          "company": job["company"], "score": m["score"],
                          "reasons": reasons,
                          "policy_snapshot": (state.get("policy") or {})
                          .get("snapshot_id")})
        return {"status": "refused", "reasons": reasons,
                "receipt_seq": r["seq"],
                "note": "Refusals are first-class outcomes — nothing was "
                        "prepared, and the refusal itself is on the record."}
    t = tailor_resume(profile, job)
    if not t["proof"]["truthful"]:
        r = emit_receipt(state, "application_refused",
                         {"job_id": job["job_id"],
                          "reasons": ["TRUTHFULNESS: tailored resume failed "
                                      "verification — " +
                                      "; ".join(t["proof"]["violations"])]})
        return {"status": "refused",
                "reasons": ["truthfulness verification failed"],
                "receipt_seq": r["seq"]}
    app_id = "app_" + uuid.uuid4().hex[:10]
    app = {"id": app_id, "job_id": job["job_id"], "job": job,
           "status": "prepared", "history": ["prepared"],
           "match": m, "tailored_text": t["tailored_text"],
           "diff_hash": t["diff_hash"], "truthfulness": t["proof"],
           "skills_emphasized": t["skills_emphasized"],
           "resume_version": profile["version"],
           "policy_snapshot": state["policy"]["snapshot_id"],
           "created_at": now(), "submitted_day": None, "payload": None}
    state["applications"][app_id] = app
    r = emit_receipt(state, "application_prepared",
                     {"application_id": app_id, "job_id": job["job_id"],
                      "title": job["title"], "company": job["company"],
                      "score": m["score"], "rationale": m["rationale"],
                      "resume_version": profile["version"],
                      "diff_hash": t["diff_hash"],
                      "truthfulness": t["proof"],
                      "policy_snapshot": app["policy_snapshot"]})
    return {"status": "prepared", "application_id": app_id,
            "match": m, "diff_hash": t["diff_hash"],
            "truthfulness": t["proof"], "receipt_seq": r["seq"],
            "note": "Review-first: nothing is submitted until the owner "
                    "calls approve_application."}


def t_approve_application(state, args):
    app = state["applications"].get(args.get("application_id", ""))
    if not app:
        raise ToolError(f"unknown application {args.get('application_id')}")
    if app["status"] != "prepared":
        raise ToolError(f"application is '{app['status']}', not 'prepared'")
    # Re-check policy at approval time — grants can expire / pause mid-queue
    allowed, reasons = check_policy(state, app["job"], app["match"])
    if not allowed:
        r = emit_receipt(state, "application_refused",
                         {"application_id": app["id"],
                          "job_id": app["job_id"],
                          "stage": "approval", "reasons": reasons})
        return {"status": "refused", "reasons": reasons,
                "receipt_seq": r["seq"]}
    app["status"] = "approved"
    app["history"].append("approved")
    emit_receipt(state, "application_approved",
                 {"application_id": app["id"], "job_id": app["job_id"],
                  "approved_by": "owner"})
    if app["job"]["source"] != "greenhouse":
        return {"status": "approved", "application_id": app["id"],
                "note": f"v0.1 dry-run submission adapter covers Greenhouse "
                        f"only; this job is from {app['job']['source']} — "
                        f"approved and held, nothing transmitted."}
    payload = build_greenhouse_payload(state, app)
    app["payload"] = payload
    if not payload["validation"]["valid"]:
        raise ToolError("payload validation failed: " +
                        "; ".join(payload["validation"]["problems"]))
    app["status"] = "submitted_dry_run"
    app["history"].append("submitted_dry_run")
    app["submitted_day"] = today()
    r = emit_receipt(state, "application_submitted_dry_run",
                     {"application_id": app["id"], "job_id": app["job_id"],
                      "title": app["job"]["title"],
                      "company": app["job"]["company"],
                      "resume_version": app["resume_version"],
                      "diff_hash": app["diff_hash"],
                      "policy_snapshot": app["policy_snapshot"],
                      "payload": payload,
                      "transmitted": False,
                      "plain_language": f"DRY-RUN: a Greenhouse application "
                      f"for {app['job']['title']} at {app['job']['company']} "
                      f"was built and validated but NOT sent. No employer "
                      f"received anything."})
    return {"status": "submitted_dry_run", "application_id": app["id"],
            "receipt_seq": r["seq"], "payload_validation": payload["validation"],
            "transmitted": False,
            "note": "DRY-RUN — payload constructed, validated and recorded. "
                    "Nothing was transmitted to the employer."}


def t_reject_application(state, args):
    app = state["applications"].get(args.get("application_id", ""))
    if not app:
        raise ToolError(f"unknown application {args.get('application_id')}")
    if app["status"] not in ("prepared", "approved"):
        raise ToolError(f"cannot reject an application in state "
                        f"'{app['status']}'")
    app["status"] = "rejected"
    app["history"].append("rejected")
    r = emit_receipt(state, "application_rejected",
                     {"application_id": app["id"], "job_id": app["job_id"],
                      "reason": args.get("reason", "owner rejected")})
    return {"status": "rejected", "application_id": app["id"],
            "receipt_seq": r["seq"]}


def t_list_receipts(state, args):
    ok = verify_receipt_chain(state["receipts"])
    limit = int(args.get("limit", 50))
    return {"receipts": state["receipts"][-limit:],
            "total": len(state["receipts"]), "chain_valid": ok,
            "mode": SUBMISSION_MODE}


def t_get_tracker(state, args):
    apps = [{"id": a["id"], "job_id": a["job_id"], "title": a["job"]["title"],
             "company": a["job"]["company"], "status": a["status"],
             "history": a["history"], "score": a["match"]["score"],
             "diff_hash": a["diff_hash"], "created_at": a["created_at"]}
            for a in state["applications"].values()]
    refused = sum(1 for r in state["receipts"]
                  if r["type"] == "application_refused")
    return {"applications": apps, "refusals_on_record": refused,
            "counts": {s: sum(1 for a in apps if a["status"] == s)
                       for s in ("prepared", "approved", "rejected",
                                 "submitted_dry_run", "closed")},
            "submission_mode": SUBMISSION_MODE}


def t_pause(state, args):
    state["paused"] = True
    r = emit_receipt(state, "paused",
                     {"reason": args.get("reason", "owner paused"),
                      "note": "Kill switch: no prepares, no submissions."})
    return {"paused": True, "receipt_seq": r["seq"]}


def t_resume(state, args):
    state["paused"] = False
    r = emit_receipt(state, "resumed", {"by": "owner"})
    return {"paused": False, "receipt_seq": r["seq"]}


def t_delete_my_data(state, args):
    if args.get("confirm") is not True:
        raise ToolError("delete_my_data requires confirm=true — this "
                        "purges the profile, policy, applications and the "
                        "entire receipts ledger")
    counts = {"profile": 1 if state.get("profile") else 0,
              "policy": 1 if state.get("policy") else 0,
              "applications": len(state["applications"]),
              "receipts": len(state["receipts"]),
              "cached_jobs": len(state["jobs"])}
    fresh = empty_state()
    state.clear()
    state.update(fresh)
    return {"purged": counts, "verified_empty": state == empty_state(),
            "note": "All personal data deleted from the local state file. "
                    "The ledger is gone by design — deletion beats audit "
                    "when the owner says delete."}


TOOLS = {
    "ingest_resume": (t_ingest_resume,
                      "Parse a plain-text/markdown resume into a structured profile, verbatim — the parser structures, never embellishes. (Facade addition: the concept's tool list omitted profile loading.)",
                      {"resume_text": {"type": "string"},
                       "resume_path": {"type": "string"}}, []),
    "set_policy": (t_set_policy,
                   "The owner grants the application scope in writing: target roles, salary floor, locations, remote, allowlist/blocklist, daily cap, approval threshold, expiry. Returns the grant text. Nothing applies outside it.",
                   {"target_roles": {"type": "array", "items": {"type": "string"}},
                    "salary_floor": {"type": "number"},
                    "locations": {"type": "array", "items": {"type": "string"}},
                    "remote_ok": {"type": "boolean"},
                    "allowlist": {"type": "array", "items": {"type": "string"}},
                    "blocklist": {"type": "array", "items": {"type": "string"}},
                    "daily_cap": {"type": "integer"},
                    "approval_threshold": {"type": "integer"},
                    "expires_in_days": {"type": "integer"}}, []),
    "get_policy": (t_get_policy, "Current policy, pause state, today's submission count, submission mode.",
                   {}, []),
    "search_jobs": (t_search_jobs,
                    "Pull REAL public JSON feeds (Greenhouse/Lever/Ashby/Workable boards) and cache normalized jobs.",
                    {"boards": {"type": "array", "items": {"type": "object"}},
                     "query": {"type": "string"},
                     "limit": {"type": "integer"}}, ["boards"]),
    "match_jobs": (t_match_jobs,
                   "Score every cached job vs profile+policy with a fully explained rationale (title/skills/comp/location) and policy eligibility.",
                   {"limit": {"type": "integer"}}, []),
    "prepare_application": (t_prepare_application,
                            "Policy-check, then build a PREPARED application: tailored resume (verbatim facts, re-ordered) + verifiable diff. Policy failures return a recorded refusal with reasons.",
                            {"job_id": {"type": "string"}}, ["job_id"]),
    "approve_application": (t_approve_application,
                            "Owner approves a prepared application. For Greenhouse jobs this builds + validates the submission payload and records a DRY-RUN submission receipt. v0.1 transmits NOTHING.",
                            {"application_id": {"type": "string"}}, ["application_id"]),
    "reject_application": (t_reject_application,
                           "Owner rejects a prepared/approved application; the rejection is receipted.",
                           {"application_id": {"type": "string"},
                            "reason": {"type": "string"}}, ["application_id"]),
    "list_receipts": (t_list_receipts,
                      "The append-only, hash-chained receipts ledger: grants, prepares, approvals, refusals, dry-run submissions, pause/resume.",
                      {"limit": {"type": "integer"}}, []),
    "get_tracker": (t_get_tracker,
                    "Application states: prepared -> approved -> submitted_dry_run -> closed, plus refusal count.",
                    {}, []),
    "pause": (t_pause, "KILL SWITCH: pause everything instantly. Prepares and submissions stop; the pause is receipted.",
              {"reason": {"type": "string"}}, []),
    "resume": (t_resume, "Owner lifts a pause. Receipted.", {}, []),
    "delete_my_data": (t_delete_my_data,
                       "Verified purge of profile, policy, applications, cached jobs and the receipts ledger. Requires confirm=true.",
                       {"confirm": {"type": "boolean"}}, ["confirm"]),
}


def tool_list():
    return [{"name": name, "description": desc,
             "inputSchema": {"type": "object", "properties": props,
                             "required": req}}
            for name, (_, desc, props, req) in TOOLS.items()]


def handle(req: dict):
    method, rid = req.get("method", ""), req.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": req.get("params", {}).get(
                "protocolVersion", "2025-03-26"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "eclipse-apply", "version": "0.1.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": rid, "result": {"tools": tool_list()}}
    if method == "tools/call":
        name = req["params"]["name"]
        args = req["params"].get("arguments", {})
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid, "error": {
                "code": -32602, "message": f"unknown tool {name}"}}
        state = load_state()
        try:
            out = TOOLS[name][0](state, args)
            save_state(state)
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps(out, indent=1)}]}}
        except ToolError as e:
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text",
                             "text": json.dumps({"error": str(e)})}],
                "isError": True}}
    if rid is not None:
        return {"jsonrpc": "2.0", "id": rid, "error": {
            "code": -32601, "message": f"method not found: {method}"}}
    return None  # notification


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            resp = handle(json.loads(line))
        except Exception as e:  # never crash the transport
            resp = {"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
