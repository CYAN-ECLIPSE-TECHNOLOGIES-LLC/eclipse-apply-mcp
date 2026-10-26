# Eclipse Apply MCP

**Powered by Cyan Eclipse Technologies** — https://cetech.ai
For listing on Eclipse Bazaar (eclipsebazaar.com) — status: in development; CERTIFIED(sandbox) — Eclipse Bazaar Test Bench 2026-10-25, 17/18 checks pass, 1 na (sandbox environment; see Status).

## Why this exists

The auto-apply category competes on volume: spray a thousand applications
and hope. Recruiters have responded with pattern detection, fraud signals
and AI screening — fully automated tools now convert at roughly 1–6%, and
the category leaders are retreating to human-in-the-loop autofill. Worse,
several shipped tools have been caught *inventing* resume content.

Eclipse Apply is the opposite design — the Eclipse Agent Wallet pattern
applied to job hunting: **policy-scoped autonomy with receipts.**

- **The grant is the product.** The owner writes the scope: target roles,
  salary floor, locations/remote, company allowlist + blocklist, daily cap,
  approval threshold, expiry, pause/kill switch. Nothing is prepared or
  submitted outside it. Refusals are first-class outcomes, each one
  receipted with the clause that refused.
- **Review-first.** Matches land as *prepared* applications. A human
  approves before anything moves. (In v0.1, approval triggers a dry-run —
  see Status.)
- **Truthfulness is machine-checked, not promised.** Tailoring re-orders
  and re-emphasizes *verbatim* resume facts only. Every tailored resume
  ships with a diff hash and a verification proof: every bullet, role
  heading and skill is checked against the source resume. Zero added
  facts, or it does not ship.
- **A receipt for everything.** Grants, prepares, approvals, rejections,
  refusals and (dry-run) submissions land in an append-only, hash-chained
  ledger: what was sent, to whom, when, which resume version, under which
  policy snapshot.

Positioning: *the auto-apply tool that shows its work.*

## Status — read this before the feature list

**v0.1.0, in development.** Honest scope:

- **Discovery is LIVE and real**: public JSON feeds from Greenhouse,
  Lever, Ashby and Workable boards (the same unauthenticated feeds that
  power company careers pages). Smoke-verified against real boards
  (Stripe, Ramp, Palantir — 1,195 jobs pulled in the recorded run).
  Workable live-verified 2026-10-26 (EB-03, `smoke-workable.py` vs the
  Epignosis board: 12 postings, every normalized record cross-checked
  against the raw widget payload). That smoke caught two real adapter
  defects, both fixed + regression-tested: the live payload's remote
  flag is `telecommuting`, not `telework`; and multi-location postings
  share one shortcode, which collided in the job cache (now suffixed
  per location). Approving a non-Greenhouse application returns a
  structured APPROVED-AND-HELD response (`held`, `hold_reason`,
  `transmitted: false`, `receipt_seq`) — the application stays in
  `approved` for owner review and nothing transmits.
- **Submission is DRY-RUN ONLY.** The Greenhouse adapter constructs and
  validates the exact submission payload, records it in the receipt, and
  **transmits nothing**. There is deliberately no code path in v0.1 that
  posts an application to an employer. Every receipt is stamped
  `DRY-RUN`; every submission receipt states `transmitted: false`.
  Test-submitting real applications to real employers would be spam —
  so v0.1 doesn't.
- **Bench verdict: CERTIFIED(sandbox).** Eclipse Bazaar Test Bench
  2026-10-25: 17 pass, 1 na of 18 checks (scorecard:
  `test-bench/results/eclipse-apply-mcp-2026-10-25.json`). Scope is
  sandbox only — bench jobs were synthetic fixtures, no live boards
  were fetched and no employer was contacted; the auth check was na
  (local stdio transport has no credential layer to test).
- Local state only (`state.json`); no accounts, no network calls except
  the public discovery feeds.

### Live-submit unlock criteria (all required, in order)

1. Test Bench CERTIFIED verdict for the policy/truthfulness suite.
2. A named pilot user applying to *their own* chosen boards, review-first
   mode only, daily cap ≤ 5.
3. Pilot gate from the concept spec: ≥5% interview rate and zero
   pattern/fraud flags across the pilot cohort — else the product pivots
   to assisted-only and live submit never ships.
4. LinkedIn and Indeed submission are **permanently out of scope**
   (their terms prohibit automating applications; the account at risk is
   the user's whole professional network).

## Tools (13)

| Tool | What it does |
|---|---|
| `ingest_resume` | Parse a text/markdown resume into a structured profile — verbatim; the parser structures, never embellishes. |
| `set_policy` | Owner grants the application scope in writing; returns the grant text + snapshot id. |
| `get_policy` | Current policy, pause state, today's submission count. |
| `search_jobs` | Pull real public feeds for `[{source, board}]` and cache normalized jobs. |
| `match_jobs` | Score cached jobs vs profile+policy — every point explained (title / skills / comp / location), plus policy eligibility. |
| `prepare_application` | Policy-check → tailored resume + verifiable diff. Policy failures return a *recorded refusal with reasons*. |
| `approve_application` | Owner approves: Greenhouse jobs get a built + validated payload and a DRY-RUN submission receipt. Nothing is transmitted. |
| `reject_application` | Owner rejects a prepared application; receipted. |
| `list_receipts` | The append-only, hash-chained ledger (integrity verified on read). |
| `get_tracker` | States: prepared → approved → submitted_dry_run → closed, plus refusal count. |
| `pause` | Kill switch — everything stops instantly; receipted. |
| `resume` | Owner lifts a pause; receipted. |
| `delete_my_data` | Verified purge of profile, policy, applications and the whole ledger. Requires `confirm=true`. |

## Run it

```bash
python3 server.py            # MCP over stdio, stdlib only, no dependencies
python3 -m unittest discover -s tests   # 22 tests
python3 smoke.py             # live smoke vs real boards (writes smoke-output.json)
python3 smoke-workable.py    # EB-03 live Workable-board smoke (writes smoke-workable-output.json)
```

State persists in `state.json` beside the server (override with
`ECLIPSE_APPLY_STATE`).

### Use it with your Muse (custom connector / local MCP)

Point your agent's MCP configuration at `python3 /path/to/server.py` —
any MCP client (Muse, Muse, Cursor) can drive it. Worked
example:

1. `ingest_resume {resume_text: "…"}` → profile structured verbatim.
2. `set_policy {target_roles: ["Backend Engineer"], salary_floor: 120000,
   blocklist: ["BadCo"], daily_cap: 5, approval_threshold: 60}` → grant issued.
3. `search_jobs {boards: [{source: "greenhouse", board: "stripe"}]}` → real jobs.
4. `match_jobs` → ranked matches, each with its rationale spelled out.
5. `prepare_application {job_id}` → tailored resume + diff proof, or a
   refusal with the policy clause that stopped it.
6. `approve_application {application_id}` → DRY-RUN receipt. In v0.1 the
   employer receives nothing — that is the point of v0.1.

## Privacy bar

Resumes are PII. v0.1 keeps everything in one local state file under the
user's control, makes no network calls beyond public job feeds, stores no
credentials, trains on nothing, and offers `delete_my_data` — a verified,
one-call purge of profile, policy, applications and receipts. (Hosted
tiers, when they exist, must pass the same bar plus encryption at rest
before any Test Bench verdict.)

## License / attribution

A Cyan Eclipse Technologies product. The "Powered by Cyan Eclipse
Technologies" attribution travels with it.
