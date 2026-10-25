#!/usr/bin/env python3
"""Eclipse Apply v0.1 — LIVE smoke test.

Drives the real MCP server over stdio JSON-RPC against REAL public job
boards, with the SYNTHETIC fixture persona (fixtures/synthetic_resume.md —
not a real person). Discovery is live; submission is DRY-RUN (nothing is
transmitted to any employer — v0.1 has no transmit code path).

Usage: python3 smoke.py   (writes smoke-output.json beside this file)
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
CANDIDATE_BOARDS = [
    {"source": "greenhouse", "board": "stripe"},
    {"source": "ashby", "board": "ramp"},
    {"source": "lever", "board": "palantir"},
    {"source": "greenhouse", "board": "airbnb"},
    {"source": "ashby", "board": "openai"},
]


class Client:
    def __init__(self, state_path):
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


def main():
    evidence = {"boards": [], "steps": {}}
    with tempfile.TemporaryDirectory() as td:
        c = Client(Path(td) / "smoke-state.json")
        tools = c.call("tools/list", raw=True)["result"]["tools"]
        evidence["tool_count"] = len(tools)
        evidence["tools"] = [t["name"] for t in tools]

        fixture = (HERE / "fixtures" / "synthetic_resume.md").read_text()
        out = c.tool("ingest_resume", {"resume_text": fixture})
        evidence["steps"]["ingest"] = {
            "name": out["profile"]["name"],
            "skills": len(out["profile"]["skills"]),
            "roles": len(out["profile"]["experience"]),
            "version": out["profile"]["version"]}

        # Live discovery: try candidates until >= 2 boards return jobs
        ok_boards = []
        for b in CANDIDATE_BOARDS:
            for attempt in range(2):
                try:
                    r = c.tool("search_jobs", {"boards": [b], "limit": 5})
                    got = r["discovered"][0]["jobs"] if r["discovered"] else 0
                    if got:
                        ok_boards.append(b)
                        evidence["boards"].append({**b, "jobs": got})
                        break
                    if r["errors"]:
                        evidence["boards"].append({**b, "error":
                                                   r["errors"][0]["error"]})
                        break
                except Exception as e:
                    if attempt == 1:
                        evidence["boards"].append({**b, "error": str(e)[:150]})
            if len(ok_boards) >= 3:
                break
        evidence["boards_ok"] = len(ok_boards)
        assert len(ok_boards) >= 2, "need >= 2 live boards for smoke pass"

        # Pull one board fully so matching has real volume
        c.tool("search_jobs", {"boards": [ok_boards[0]], "limit": 200})

        pol = {"target_roles": ["Backend Engineer", "Software Engineer"],
               "salary_floor": 100000, "remote_ok": True,
               "daily_cap": 5, "approval_threshold": 40}
        out = c.tool("set_policy", pol)
        evidence["steps"]["policy_snapshot"] = out["policy"]["snapshot_id"]

        matches = c.tool("match_jobs", {"limit": 10})["matches"]
        evidence["steps"]["top_match"] = {
            "title": matches[0]["title"], "company": matches[0]["company"],
            "score": matches[0]["score"], "rationale": matches[0]["rationale"]}

        # Policy refusal on a REAL job: blocklist the top match's company
        victim = matches[0]
        c.tool("set_policy", {**pol, "blocklist": [victim["company"]]})
        refusal = c.tool("prepare_application", {"job_id": victim["job_id"]})
        evidence["steps"]["refusal"] = refusal
        assert refusal["status"] == "refused"

        # Restore grant, prepare the best eligible Greenhouse job if present
        c.tool("set_policy", pol)
        matches = c.tool("match_jobs", {"limit": 50})["matches"]
        eligible = [m for m in matches if m["policy_eligible"]]
        gh = [m for m in eligible
              if m["job_id"].startswith("greenhouse:")]
        target = (gh or eligible)[0]
        prep = c.tool("prepare_application", {"job_id": target["job_id"]})
        evidence["steps"]["prepared"] = {
            "status": prep["status"], "diff_hash": prep.get("diff_hash"),
            "truthful": (prep.get("truthfulness") or {}).get("truthful")}
        assert prep["status"] == "prepared"
        assert prep["truthfulness"]["truthful"] is True

        appr = c.tool("approve_application",
                      {"application_id": prep["application_id"]})
        evidence["steps"]["approval"] = {
            "status": appr["status"], "transmitted": appr.get("transmitted")}
        receipts = c.tool("list_receipts", {"limit": 100})
        evidence["steps"]["receipts_total"] = receipts["total"]
        evidence["steps"]["receipt_chain_valid"] = receipts["chain_valid"]
        dry = [r for r in receipts["receipts"]
               if r["type"] == "application_submitted_dry_run"]
        if dry:
            d = dry[0]["detail"]
            evidence["steps"]["dry_run_receipt"] = {
                "mode": dry[0]["mode"], "transmitted": d["transmitted"],
                "plain_language": d["plain_language"],
                "diff_hash": d["diff_hash"],
                "policy_snapshot": d["policy_snapshot"]}
        tracker = c.tool("get_tracker")
        evidence["steps"]["tracker_counts"] = tracker["counts"]
        evidence["steps"]["refusals_on_record"] = tracker["refusals_on_record"]
        c.close()
    (HERE / "smoke-output.json").write_text(json.dumps(evidence, indent=1))
    print(json.dumps(evidence, indent=1))
    print("SMOKE PASS" if evidence["boards_ok"] >= 2 else "SMOKE FAIL")


if __name__ == "__main__":
    main()
