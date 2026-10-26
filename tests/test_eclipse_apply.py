#!/usr/bin/env python3
# Eclipse Apply MCP
# Copyright © 2026 Cyan Eclipse Technologies LLC · cetech.ai
# Eclipse Apply v0.1 — unit tests. Run: python3 -m unittest discover tests

"""Eclipse Apply v0.1 — unit tests. Run: python3 -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
import discovery  # noqa: E402
import server as S  # noqa: E402

FIXTURE = (Path(__file__).parent.parent / "fixtures" /
           "synthetic_resume.md").read_text()

GOOD_JOB = {"job_id": "greenhouse:fictional:1", "source": "greenhouse",
            "board": "fictional", "company": "Fictional Labs Inc",
            "title": "Senior Backend Engineer", "location": "Remote - US",
            "remote": True, "comp_min": 150000, "comp_max": 190000,
            "comp_currency": "USD", "url": "https://example.com/job/1",
            "posted_date": "2026-10-20",
            "description_text": "Python Go PostgreSQL Docker Kubernetes "
                                "REST APIs AWS CI/CD microservices"}


def fresh_state(with_profile=True, policy_args=None):
    st = S.empty_state()
    if with_profile:
        S.t_ingest_resume(st, {"resume_text": FIXTURE})
    if policy_args is not None:
        S.t_set_policy(st, policy_args)
    return st


BASE_POLICY = {"target_roles": ["Backend Engineer"], "salary_floor": 120000,
               "locations": [], "remote_ok": True, "daily_cap": 5,
               "approval_threshold": 50}


class TestAdapters(unittest.TestCase):
    def test_greenhouse(self):
        payload = {"jobs": [{"id": 42, "title": "Backend Engineer",
                             "location": {"name": "Remote"},
                             "absolute_url": "https://gh/42",
                             "updated_at": "2026-10-01T00:00:00Z",
                             "content": "<p>Python APIs</p>"}]}
        jobs = discovery.greenhouse("acme", lambda u: payload)
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["job_id"], "greenhouse:acme:42")
        self.assertTrue(j["remote"])
        self.assertIsNone(j["comp_max"])  # never guessed
        self.assertNotIn("<p>", j["description_text"])

    def test_lever(self):
        payload = [{"id": "x1", "text": "Backend Engineer",
                    "categories": {"location": "Berlin"},
                    "hostedUrl": "https://lever/x1", "createdAt": 1760000000000,
                    "salaryRange": {"min": 90000, "max": 120000,
                                    "currency": "EUR"},
                    "descriptionPlain": "Go services"}]
        jobs = discovery.lever("acme", lambda u: payload)
        self.assertEqual(jobs[0]["comp_max"], 120000)
        self.assertEqual(jobs[0]["location"], "Berlin")

    def test_ashby(self):
        payload = {"jobs": [{"id": "a1", "title": "Backend Engineer",
                             "location": "Remote", "isRemote": True,
                             "jobUrl": "https://ashby/a1",
                             "publishedAt": "2026-09-01T00:00:00Z",
                             "descriptionHtml": "<p>Kubernetes</p>",
                             "compensation": {
                                 "scrapeableCompensationSalarySummary":
                                 "$130K – $170K • Offers Equity"}}]}
        jobs = discovery.ashby("acme", lambda u: payload)
        self.assertEqual(jobs[0]["comp_min"], 130000)
        self.assertEqual(jobs[0]["comp_max"], 170000)
        self.assertEqual(jobs[0]["comp_currency"], "USD")
        self.assertTrue(jobs[0]["remote"])

    def test_workable(self):
        payload = {"name": "Acme Co", "jobs": [
            {"shortcode": "W1", "title": "Backend Engineer",
             "city": "Austin", "country": "US", "telework": False,
             "url": "https://workable/W1", "created_at": "2026-08-15",
             "description": "Python"}]}
        jobs = discovery.workable("acme", lambda u: payload)
        self.assertEqual(jobs[0]["company"], "Acme Co")
        self.assertEqual(jobs[0]["location"], "Austin, US")
        self.assertFalse(jobs[0]["remote"])

    def test_workable_duplicate_shortcode_locations(self):
        # Live boards reuse one shortcode across locations (EB-03,
        # 2026-10-26). Each location must survive as its own record.
        payload = {"name": "Multi Co", "jobs": [
            {"shortcode": "DUP1", "title": "Software Engineer",
             "city": "Athens", "country": "Greece", "telecommuting": False,
             "url": "https://apply.workable.com/j/DUP1",
             "created_at": "2026-10-01"},
            {"shortcode": "DUP1", "title": "Software Engineer",
             "city": "Heraklion", "country": "Greece",
             "telecommuting": False,
             "url": "https://apply.workable.com/j/DUP1",
             "created_at": "2026-10-01"}]}
        jobs = discovery.workable("multico", lambda u: payload)
        self.assertEqual(len(jobs), 2)
        ids = [j["job_id"] for j in jobs]
        self.assertEqual(len(set(ids)), 2)
        self.assertIn("workable:multico:DUP1-athens", ids)
        self.assertIn("workable:multico:DUP1-heraklion", ids)
        self.assertEqual({j["location"] for j in jobs},
                         {"Athens, Greece", "Heraklion, Greece"})

    def test_workable_telecommuting_flag(self):
        # Real widget payloads (EB-03 live smoke, 2026-10-26) carry NO
        # "telework" key and NO description — the remote flag is
        # "telecommuting". A regression here silently marks real remote
        # jobs on-site.
        payload = {"name": "Real Co", "jobs": [
            {"shortcode": "W2", "title": "Support Engineer",
             "city": "Athens", "country": "Greece", "telecommuting": True,
             "url": "https://apply.workable.com/j/W2",
             "created_at": "2026-10-01"},
            {"shortcode": "W3", "title": "Office Manager",
             "city": "Athens", "country": "Greece", "telecommuting": False,
             "url": "https://apply.workable.com/j/W3",
             "created_at": "2026-10-01"}]}
        jobs = discovery.workable("realco", lambda u: payload)
        self.assertTrue(jobs[0]["remote"])
        self.assertFalse(jobs[1]["remote"])
        self.assertEqual(jobs[0]["description_text"], "")
        self.assertEqual(jobs[0]["posted_date"], "2026-10-01")


class TestResumeParse(unittest.TestCase):
    def test_verbatim_structure(self):
        p = S.parse_resume(FIXTURE)
        self.assertEqual(p["name"], "Jordan Example")
        self.assertIn("Python", p["skills"])
        self.assertEqual(len(p["experience"]), 3)
        self.assertEqual(p["experience"][0]["company"], "Fictional Labs Inc")
        for e in p["experience"]:
            for b in e["bullets"]:
                self.assertIn(b, FIXTURE)  # every bullet verbatim in source


class TestPolicyRefusals(unittest.TestCase):
    def test_blocklist_refusal_via_prepare(self):
        pol = dict(BASE_POLICY, blocklist=["Fictional Labs Inc"])
        st = fresh_state(policy_args=pol)
        st["jobs"][GOOD_JOB["job_id"]] = GOOD_JOB
        out = S.t_prepare_application(st, {"job_id": GOOD_JOB["job_id"]})
        self.assertEqual(out["status"], "refused")
        self.assertTrue(any("BLOCKLIST" in r for r in out["reasons"]))
        types = [r["type"] for r in st["receipts"]]
        self.assertIn("application_refused", types)

    def test_salary_floor(self):
        st = fresh_state(policy_args=BASE_POLICY)
        job = dict(GOOD_JOB, comp_min=80000, comp_max=100000)
        ok, reasons = S.check_policy(st, job)
        self.assertFalse(ok)
        self.assertTrue(any("SALARY FLOOR" in r for r in reasons))

    def test_daily_cap(self):
        st = fresh_state(policy_args=dict(BASE_POLICY, daily_cap=1))
        st["applications"]["app_x"] = {"submitted_day": S.today()}
        ok, reasons = S.check_policy(st, GOOD_JOB)
        self.assertFalse(ok)
        self.assertTrue(any("DAILY CAP" in r for r in reasons))

    def test_expiry(self):
        st = fresh_state(policy_args=BASE_POLICY)
        st["policy"]["expires_at"] = S.now() - 10
        ok, reasons = S.check_policy(st, GOOD_JOB)
        self.assertFalse(ok)
        self.assertTrue(any("EXPIRED" in r for r in reasons))

    def test_paused(self):
        st = fresh_state(policy_args=BASE_POLICY)
        S.t_pause(st, {"reason": "test"})
        ok, reasons = S.check_policy(st, GOOD_JOB)
        self.assertFalse(ok)
        self.assertTrue(any("PAUSED" in r for r in reasons))

    def test_allowlist(self):
        st = fresh_state(policy_args=dict(BASE_POLICY,
                                          allowlist=["Other Co"]))
        ok, reasons = S.check_policy(st, GOOD_JOB)
        self.assertFalse(ok)
        self.assertTrue(any("ALLOWLIST" in r for r in reasons))

    def test_no_policy(self):
        st = fresh_state(policy_args=None)
        ok, reasons = S.check_policy(st, GOOD_JOB)
        self.assertFalse(ok)
        self.assertTrue(any("NO POLICY" in r for r in reasons))


class TestMatchingTruthfulness(unittest.TestCase):
    def test_match_explained(self):
        st = fresh_state(policy_args=BASE_POLICY)
        m = S.match_job(st["profile"], GOOD_JOB, st["policy"])
        self.assertGreaterEqual(m["score"], 50)
        self.assertEqual(len(m["rationale"]), 4)
        s = m["signals"]
        self.assertEqual(m["score"], s["title_fit"] + s["skills_overlap"]
                         + s["comp"] + s["location"])

    def test_tailor_adds_zero_facts(self):
        st = fresh_state(policy_args=BASE_POLICY)
        t = S.tailor_resume(st["profile"], GOOD_JOB)
        self.assertTrue(t["proof"]["truthful"], t["proof"]["violations"])
        src_bullets = {b for e in st["profile"]["experience"]
                       for b in e["bullets"]}
        tt = S.parse_resume(t["tailored_text"])
        for e in tt["experience"]:
            for b in e["bullets"]:
                self.assertIn(b, src_bullets)

    def test_checker_catches_fabrication(self):
        st = fresh_state(policy_args=BASE_POLICY)
        t = S.tailor_resume(st["profile"], GOOD_JOB)
        poisoned = t["tailored_text"].replace(
            "- Built Python and Go microservices handling 40M API requests per day",
            "- Led a 500-person org and 10x'd revenue at FAANG")
        proof = S.verify_truthful(st["profile"], poisoned)
        self.assertFalse(proof["truthful"])
        self.assertTrue(proof["violations"])


class TestFlowAndReceipts(unittest.TestCase):
    def test_prepare_approve_dry_run(self):
        st = fresh_state(policy_args=BASE_POLICY)
        st["jobs"][GOOD_JOB["job_id"]] = GOOD_JOB
        prep = S.t_prepare_application(st, {"job_id": GOOD_JOB["job_id"]})
        self.assertEqual(prep["status"], "prepared")
        out = S.t_approve_application(st, {"application_id":
                                           prep["application_id"]})
        self.assertEqual(out["status"], "submitted_dry_run")
        self.assertFalse(out["transmitted"])
        self.assertTrue(out["payload_validation"]["valid"])
        app = st["applications"][prep["application_id"]]
        self.assertEqual(app["history"],
                         ["prepared", "approved", "submitted_dry_run"])
        sub = [r for r in st["receipts"]
               if r["type"] == "application_submitted_dry_run"]
        self.assertEqual(len(sub), 1)
        self.assertEqual(sub[0]["mode"], "DRY-RUN")
        self.assertFalse(sub[0]["detail"]["transmitted"])
        self.assertIn("NOT sent", sub[0]["detail"]["plain_language"])

    def test_approve_non_greenhouse_hold(self):
        # EB-03 polish: approving a non-Greenhouse application returns a
        # structured hold (not a bare note) and transmits nothing.
        wk_job = dict(GOOD_JOB, job_id="workable:acme:W1",
                      source="workable", board="acme")
        st = fresh_state(policy_args=BASE_POLICY)
        st["jobs"][wk_job["job_id"]] = wk_job
        prep = S.t_prepare_application(st, {"job_id": wk_job["job_id"]})
        self.assertEqual(prep["status"], "prepared")
        out = S.t_approve_application(st, {"application_id":
                                           prep["application_id"]})
        self.assertEqual(out["status"], "approved")
        self.assertTrue(out["held"])
        self.assertEqual(out["hold_reason"], "no_submission_adapter_v0.1")
        self.assertEqual(out["source"], "workable")
        self.assertFalse(out["transmitted"])
        self.assertEqual(out["submission_mode"], "DRY-RUN")
        self.assertIn("NOT sent", out["plain_language"])
        app = st["applications"][prep["application_id"]]
        self.assertEqual(app["status"], "approved")
        self.assertEqual(app["history"], ["prepared", "approved"])
        self.assertEqual(app["hold"]["source"], "workable")
        self.assertFalse(app["hold"]["transmitted"])
        appr_seqs = [r["seq"] for r in st["receipts"]
                     if r["type"] == "application_approved"]
        self.assertEqual(out["receipt_seq"], appr_seqs[0])
        types = [r["type"] for r in st["receipts"]]
        self.assertNotIn("application_submitted_dry_run", types)
        appr = [r for r in st["receipts"]
                if r["type"] == "application_approved"][0]
        self.assertTrue(appr["detail"]["held"])
        self.assertFalse(appr["detail"]["transmitted"])
        tracker = S.t_get_tracker(st, {})
        self.assertEqual(tracker["counts"]["approved"], 1)
        self.assertEqual(tracker["applications"][0]["hold"]["source"],
                         "workable")

    def test_reject(self):
        st = fresh_state(policy_args=BASE_POLICY)
        st["jobs"][GOOD_JOB["job_id"]] = GOOD_JOB
        prep = S.t_prepare_application(st, {"job_id": GOOD_JOB["job_id"]})
        out = S.t_reject_application(st, {"application_id":
                                          prep["application_id"]})
        self.assertEqual(out["status"], "rejected")

    def test_receipt_chain_integrity(self):
        st = fresh_state(policy_args=BASE_POLICY)
        st["jobs"][GOOD_JOB["job_id"]] = GOOD_JOB
        S.t_prepare_application(st, {"job_id": GOOD_JOB["job_id"]})
        self.assertTrue(S.verify_receipt_chain(st["receipts"]))
        st["receipts"][0]["detail"]["tampered"] = True
        self.assertFalse(S.verify_receipt_chain(st["receipts"]))

    def test_delete_my_data(self):
        st = fresh_state(policy_args=BASE_POLICY)
        with self.assertRaises(S.ToolError):
            S.t_delete_my_data(st, {})
        out = S.t_delete_my_data(st, {"confirm": True})
        self.assertTrue(out["verified_empty"])
        self.assertEqual(out["purged"]["receipts"],
                         len([1]) and out["purged"]["receipts"])
        self.assertIsNone(st["profile"])
        self.assertEqual(st["receipts"], [])


if __name__ == "__main__":
    unittest.main()

# Eclipse Apply MCP · © 2026 Cyan Eclipse Technologies LLC · cetech.ai
