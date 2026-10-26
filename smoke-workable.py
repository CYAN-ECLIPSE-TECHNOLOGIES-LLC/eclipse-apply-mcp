#!/usr/bin/env python3
# Eclipse Apply MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Eclipse Apply — EB-03 Workable live-board smoke.

"""Eclipse Apply — EB-03 Workable LIVE-board smoke test.

Workable was the only discovery adapter still verified by unit tests
alone (Greenhouse/Lever/Ashby were live-verified in smoke.py). This
smoke drives the real MCP server over stdio against ONE real public
Workable board and cross-checks the adapter's normalized records
against the raw widget payload — including the remote flag, which the
live payload names "telecommuting" (the adapter originally read a
"telework" key that real payloads do not carry).

Fixture board: Epignosis (slug "epignosis") — a real public Workable
board with multiple Software Engineer postings, selected 2026-10-26.
Persona: fixtures/synthetic_resume.md (synthetic — not a real person).

READ-ONLY against the board: discovery fetches the public feed only.
Approval of the prepared Workable application exercises the v0.1
non-Greenhouse HOLD path — nothing is transmitted to any employer.

Usage: python3 smoke-workable.py  (writes smoke-workable-output.json)
"""
import json
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BOARD = {"source": "workable", "board": "epignosis"}
WIDGET_URL = ("https://apply.workable.com/api/v1/widget/accounts/"
              + BOARD["board"])
UA = "EclipseApply/0.1 (+https://cetech.ai; discovery of public job feeds)"


class Client:
    def __init__(self, state_path):
        import os
        env = dict(os.environ, ECLIPSE_APPLY_STATE=str(state_path))
        self.p = subprocess.Popen(
            [sys.executable, str(HERE / "server.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            text=True, env=env)
        self.n = 0
        self.call("initialize", {"protocolVersion": "2025-03-26"}, raw=True)

    def call(self, method, params=None, raw=False):
        self.n += 1
        req = {"jsonrpc": "2.0", "id": self.n, "method": method,
               "params": params or {}}
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        if raw:
            return resp
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return json.loads(resp["result"]["content"][0]["text"])

    def tool(self, name, args=None):
        return self.call("tools/call", {"name": name, "arguments": args or {}})

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=10)


def raw_widget():
    req = urllib.request.Request(WIDGET_URL, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        out = subprocess.run(["curl", "-s", "--fail", "--max-time", "60",
                              "-A", UA, WIDGET_URL],
                             capture_output=True, timeout=75)
        return json.loads(out.stdout.decode("utf-8", "replace"))


def main():
    raw = raw_widget()
    raw_list = raw.get("jobs", [])
    # Multi-location postings share a shortcode — key raw by (code, city)
    raw_jobs = {(j["shortcode"], j.get("city", "")): j for j in raw_list}
    evidence = {"fixture_board": {**BOARD, "company": raw.get("name"),
                                  "raw_jobs": len(raw_list),
                                  "raw_unique_shortcodes":
                                  len({j["shortcode"] for j in raw_list})},
                "steps": {}}
    assert len(raw_list) >= 5, "fixture board shrank below smoke minimum"

    with tempfile.TemporaryDirectory() as td:
        c = Client(Path(td) / "smoke-state.json")
        fixture = (HERE / "fixtures" / "synthetic_resume.md").read_text()
        c.tool("ingest_resume", {"resume_text": fixture})

        # Live discovery through the MCP server
        r = c.tool("search_jobs", {"boards": [BOARD], "limit": 200})
        assert not r["errors"], f"discovery errors: {r['errors']}"
        got = r["discovered"][0]["jobs"]
        evidence["steps"]["discovered"] = got
        assert got == len(raw_list), (got, len(raw_list))
        jobs = {j["job_id"]: j for j in r["jobs"]}
        # No cache collisions: every live posting is its own record,
        # including same-shortcode multi-location postings.
        assert len(jobs) == got, (len(jobs), got)

        # Cross-check every normalized record against the raw payload
        mismatches, remote_live = [], 0
        for jid, j in jobs.items():
            ext = jid.split(":", 2)[-1]
            code = next(c for c in {k[0] for k in raw_jobs}
                        if ext == c or ext.startswith(c + "-"))
            raw_j = raw_jobs[(code, j["location"].split(", ")[0])]
            expect_remote = bool(raw_j.get("telecommuting")) or \
                "remote" in (raw_j.get("title") or "").lower()
            if j["remote"] != expect_remote:
                mismatches.append({"job_id": jid, "field": "remote",
                                   "got": j["remote"],
                                   "expected": expect_remote})
            if j["company"] != raw.get("name"):
                mismatches.append({"job_id": jid, "field": "company"})
            if not j["url"].startswith("https://apply.workable.com/j/"):
                mismatches.append({"job_id": jid, "field": "url"})
            if j["title"] != raw_j.get("title"):
                mismatches.append({"job_id": jid, "field": "title"})
            if j["posted_date"] != (raw_j.get("created_at") or "")[:10]:
                mismatches.append({"job_id": jid, "field": "posted_date"})
            remote_live += 1 if j["remote"] else 0
        evidence["steps"]["crosscheck_mismatches"] = mismatches
        evidence["steps"]["remote_jobs_live"] = remote_live
        assert not mismatches, mismatches
        # The raw board really does carry telecommuting=True postings —
        # without the adapter fix this count reads 0.
        assert remote_live >= 1, "no remote jobs flagged on live board"
        sample = next(iter(jobs.values()))
        evidence["steps"]["sample_job"] = {
            k: sample[k] for k in ("job_id", "company", "title",
                                   "location", "remote", "url",
                                   "posted_date")}
        evidence["steps"]["description_coverage"] = {
            "note": "widget feed carries no description field; adapter "
                    "honestly yields empty description_text",
            "raw_has_description_key": any("description" in j
                                           for j in raw_list)}

        # Match + prepare the best eligible live Workable job
        pol = {"target_roles": ["Software Engineer", "Backend Engineer"],
               "salary_floor": 100000, "remote_ok": True,
               "daily_cap": 5, "approval_threshold": 40}
        c.tool("set_policy", pol)
        matches = c.tool("match_jobs", {"limit": 50})["matches"]
        eligible = [m for m in matches if m["policy_eligible"]]
        assert eligible, "no policy-eligible live Workable job"
        target = eligible[0]
        evidence["steps"]["top_eligible"] = {
            "title": target["title"], "company": target["company"],
            "score": target["score"]}
        prep = c.tool("prepare_application", {"job_id": target["job_id"]})
        assert prep["status"] == "prepared", prep
        assert prep["truthfulness"]["truthful"] is True
        evidence["steps"]["prepared"] = {
            "status": prep["status"], "diff_hash": prep["diff_hash"],
            "truthful": True}

        # Approve -> structured HOLD (non-Greenhouse polish), no transmit
        appr = c.tool("approve_application",
                      {"application_id": prep["application_id"]})
        evidence["steps"]["approval_hold"] = appr
        assert appr["status"] == "approved" and appr["held"] is True
        assert appr["transmitted"] is False
        assert appr["hold_reason"] == "no_submission_adapter_v0.1"
        assert "NOT sent" in appr["plain_language"]

        receipts = c.tool("list_receipts", {"limit": 100})
        evidence["steps"]["receipts_total"] = receipts["total"]
        evidence["steps"]["receipt_chain_valid"] = receipts["chain_valid"]
        assert receipts["chain_valid"]
        types = [x["type"] for x in receipts["receipts"]]
        assert "application_submitted_dry_run" not in types
        tracker = c.tool("get_tracker")
        evidence["steps"]["tracker_counts"] = tracker["counts"]
        assert tracker["counts"]["approved"] == 1
        assert tracker["applications"][0]["hold"]["source"] == "workable"
        c.close()

    (HERE / "smoke-workable-output.json").write_text(
        json.dumps(evidence, indent=1))
    print(json.dumps(evidence, indent=1))
    print("SMOKE-WORKABLE PASS")


if __name__ == "__main__":
    main()

# Eclipse Apply MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
