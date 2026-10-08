#!/usr/bin/env python3
"""
GENERAL P8 sub-classifier — Stage 2 of a two-stage system (drill-down, not regex labels).
Repo-agnostic: works on ANY repo's dataset, inferring the GitHub repo from each run_url.

    python3 p8_subclassify.py <dataset.csv> [out.csv]

TWO-STAGE HARMONY
-----------------
Stage 1 (../classify.py, the unified repo-agnostic classifier) assigns each run a category (incl. P8).
Stage 2 (THIS) runs ONLY on category=="P8" and decides Real / Flaky / Unknown (+confidence)
by DRILLING DOWN into each failure.

RESEARCH HEURISTICS IMPLEMENTED (each maps to a paper we gathered)
-----------------------------------------------------------------
  [An et al. 2024, ICSME] abstracted failure-SYMPTOM matching:
        deterministic crash symptom (panic / duplicate metric descriptor) => Real;
        same symptom recurring across UNRELATED changes => flaky signal.
  [DeFlaker, ICSE 2018] DIFF-REACHABILITY (patch level, this version's extension):
        fetch the PR patch and check whether the FAILING TEST'S OWN FUNCTION (or code
        it reaches) was actually modified. Test failed but its code/deps untouched by
        the diff => flaky; the failing test's function was edited => Real-leaning.
  [Cross-PR corroboration] same test fails across >=2 unrelated PRs that never touch
        .go code => the test/env is the problem.
  [Duration variance] large run-to-run duration spread on the same failing test is a
        classic flaky symptom (non-deterministic timing).
  [Meta PFS / iDFlakies] no evidence => Unknown (default bucket); "failed twice" != real.
  [Micco/Google 2016] base rate: 84% of Pass->Fail transitions are flaky (used only as a
        tie-break prior, never to override diff evidence).

CONFIDENCE
----------
  high = same-SHA rerun-to-pass gold standard observed (a SUCCESSFUL run exists on the
         exact failing commit) OR deterministic-crash reproduced.
  med  = strong drill-down evidence (diff-reachability + cross-PR) but no rerun gold standard.
  low  = single ambiguous context.
"""
import csv, re, subprocess, json, collections, sys
from datetime import datetime

_files_cache, _success_cache, _sha_cache = {}, {}, {}


def repo_from_url(url):
    m = re.search(r"github\.com/([^/]+/[^/]+)/actions", url or "")
    return m.group(1) if m else None


def gh_json(args):
    try:
        out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=40)
        return json.loads(out.stdout) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def gh_text(args):
    """For --jq calls whose output is raw text (a scalar or tsv lines), not JSON."""
    try:
        out = subprocess.run(["gh", "api", *args], capture_output=True, text=True, timeout=40)
        return out.stdout if out.returncode == 0 else None
    except Exception:
        return None


def pr_files(repo, pr):
    """Fetch changed files WITH their patch (for diff-reachability). Cached."""
    if not repo or not pr:
        return None
    key = (repo, pr)
    if key in _files_cache:
        return _files_cache[key]
    data = gh_json([f"repos/{repo}/pulls/{pr}/files", "--paginate"])
    files = None
    if isinstance(data, list):
        files = [{"filename": f.get("filename", ""), "patch": f.get("patch", "")} for f in data]
    _files_cache[key] = files
    return files


def full_sha(repo, run_id):
    """Dataset stores a truncated commit; resolve the full 40-char head_sha from the run."""
    if not repo or not run_id:
        return None
    key = (repo, run_id)
    if key in _sha_cache:
        return _sha_cache[key]
    data = gh_text([f"repos/{repo}/actions/runs/{run_id}", "--jq", ".head_sha"])
    sha = data.strip() if data and data.strip() else None
    _sha_cache[key] = sha
    return sha


def same_sha_success(repo, run_id, workflow):
    """
    Gold standard [rerun-to-pass]: did the SAME workflow SUCCEED on the exact failing
    commit? (not merely some other workflow like lint). Confirms flaky.
    """
    if not repo or not run_id:
        return None
    sha = full_sha(repo, run_id)
    if not sha:
        return None
    key = (repo, sha, workflow)
    if key in _success_cache:
        return _success_cache[key]
    data = gh_text([f"repos/{repo}/actions/runs?head_sha={sha}",
                    "--jq", ".workflow_runs[] | [.name, .path, .conclusion] | @tsv"])
    res = None
    if isinstance(data, str) and data.strip():
        res = False
        wf = (workflow or "").strip().lower()
        for line in data.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, path, concl = parts[0].strip().lower(), parts[1].strip().lower(), parts[2].strip().lower()
            # EXACT workflow identity only (name equality or path basename) — no substring,
            # so "Tests" success never counts as "E2E Tests" passing.
            if concl == "success" and (name == wf or path.rsplit("/", 1)[-1] == wf):
                res = True
                break
    _success_cache[key] = res
    return res


def touched_go_code(files):
    return None if files is None else any(f["filename"].endswith(".go") for f in files)


def touched_test_file(files):
    return None if files is None else any(
        f["filename"].endswith("_test.go") or "/e2e/" in f["filename"]
        or "test/e2e" in f["filename"] or "/smoke" in f["filename"] for f in files)


def failing_test_func_changed(files, failing_tests):
    """
    DeFlaker-style diff-reachability at PATCH level: did the diff actually add/remove a
    line inside the FAILING test's own function (or a helper it obviously reaches)?
    Returns True / False / None(NA).
    """
    if files is None or not failing_tests:
        return None
    test_files = [f for f in files if f["filename"].endswith("_test.go")]
    if not test_files:
        return False  # no test file changed at all -> failing test's code not touched
    names = {re.sub(r"/.*$", "", t) for t in failing_tests}  # strip subtest path
    for f in test_files:
        patch = f.get("patch") or ""
        changed = [ln[1:] for ln in patch.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))]
        blob = "\n".join(changed)
        for n in names:
            # the failing test's function signature or a t.Run(name...) touched in the diff
            if re.search(rf"func\s+{re.escape(n)}\b", blob) or re.search(rf"\b{re.escape(n)}\b", blob):
                return True
    return False


def evidence(e):
    e = e or ""
    return {
        "deterministic_crash": bool(re.search(r"panic:|\[panicked!\]|previously registered descriptor|duplicate metrics", e, re.I)),
        "webhook_assert": bool(re.search(r"pod-with-flavor|kubectl run", e, re.I)),
        "failed_tests": re.findall(r"---\s*FAIL:\s*(\S+)", e),
        "durations": [float(x) for x in re.findall(r"---\s*FAIL:\s*\S+\s*\(([\d.]+)s\)", e)],
    }


def is_wrapper(name):
    return bool(re.match(r"TestE2E|TestSuite|TestMain|TestAll", name or ""))


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: p8_subclassify.py <dataset.csv> [out.csv]")
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else src.replace(".csv", "_p8_subclass.csv")

    rows = [r for r in csv.DictReader(open(src)) if r["category"] == "P8"]
    if not rows:
        print(f"no P8 rows in {src}"); return

    # ---- Stage-2a: temporal recurrence + duration variance per PR ----
    by_pr = collections.defaultdict(list)
    for r in rows:
        by_pr[r["PR"]].append(r)
    recurrence, dur_var = {}, {}
    for pr, rs in by_pr.items():
        ts = sorted(datetime.fromisoformat(r["ts"].replace("Z", "+00:00")) for r in rs if r.get("ts"))
        span = (ts[-1] - ts[0]).total_seconds() / 60 if len(ts) > 1 else 0.0
        recurrence[pr] = (len(rs), span)
        durs = [d for r in rs for d in evidence(r["raw_error"])["durations"]]
        dur_var[pr] = (max(durs) - min(durs)) if len(durs) > 1 else 0.0

    # ---- cross-PR: PRs whose suite fails without touching any .go code ----
    e2e_untouched_prs = []
    for pr, rs in by_pr.items():
        if not pr:
            continue
        if any(any(is_wrapper(t) for t in evidence(r["raw_error"])["failed_tests"]) for r in rs):
            repo = repo_from_url(rs[0].get("run_url"))
            if touched_go_code(pr_files(repo, pr)) is False:
                e2e_untouched_prs.append(pr)
    cross_pr_flaky = len(e2e_untouched_prs) >= 2

    # ---- Stage-2b: per-run verdict ----
    counts = collections.Counter()
    for r in rows:
        ev = evidence(r["raw_error"])
        pr, repo = r["PR"], repo_from_url(r.get("run_url"))
        n_runs, span = recurrence.get(pr, (1, 0.0))
        var = dur_var.get(pr, 0.0)
        files = pr_files(repo, pr)
        tg = touched_go_code(files)
        func_changed = failing_test_func_changed(files, ev["failed_tests"])
        gold_pass = same_sha_success(repo, r.get("run_id"), r.get("workflow"))
        wrapper = any(is_wrapper(t) for t in ev["failed_tests"])
        leaf = [t for t in ev["failed_tests"] if not is_wrapper(t)]
        recur = f"{n_runs} fails/{span:.0f}min, dur-var~{var:.0f}s on {'PR '+pr if pr else 'push build'}"

        if ev["deterministic_crash"]:
            sub, conf = "Real", "high"
            reason = f"deterministic crash (panic/duplicate-descriptor) reproduces => Real/high; {recur} [An et al.]"
        elif func_changed is True:
            sub, conf = "Real", "med"
            reason = f"DeFlaker diff-reachability: the FAILING test's own function was edited in PR {pr} => Real; {recur}"
        elif ev["webhook_assert"]:
            sub, conf = "Unknown", "low"
            reason = f"kubectl pod-with-flavor webhook assertion; product-vs-env ambiguous; {recur}"
        elif wrapper and pr and tg is True:
            sub, conf = "Real", "med"
            reason = f"PR {pr} changed .go source (test func not directly reached) => Real-leaning; {recur}"
        elif wrapper and pr and tg is False:
            sub = "Flaky"
            conf = "high" if gold_pass else "med"
            gold = " + same-SHA rerun-to-pass CONFIRMED" if gold_pass else " (no rerun gold standard -> med)"
            reason = (f"diff-reachability: PR {pr} touched only deps/Docker/workflow (0 .go), suite still fails, "
                      f"across {len(e2e_untouched_prs)} unrelated PRs {e2e_untouched_prs}; {recur} => Flaky{gold}")
        elif wrapper:
            sub = "Flaky" if (gold_pass or (cross_pr_flaky and var >= 60)) else "Unknown"
            conf = "high" if gold_pass else "med"
            gold = " + same-SHA rerun-to-pass CONFIRMED" if gold_pass else ""
            reason = (f"suite on push build (no PR diff); duration variance ~{var:.0f}s + matches cross-PR flaky "
                      f"symptom [An et al.]{gold} => {'flaky' if sub=='Flaky' else 'Unknown'}; {recur}")
        elif leaf:
            if func_changed is True:
                sub, conf = "Real", "med"
                reason = f"leaf {leaf[:2]} failed and its function was edited in PR {pr} => Real; {recur}"
            else:
                sub, conf = "Unknown", "low"
                reason = f"leaf {leaf[:2]} failed; failing func not in any diff / push build => insufficient; {recur}"
        else:
            sub, conf = "Unknown", "low"
            reason = f"P8 with no distinguishing evidence; {recur}"

        r["p8_subclass"], r["flaky_confidence"] = sub, conf
        r["p8_touched_go_code"] = "" if tg is None else str(tg)
        r["p8_test_func_changed"] = "" if func_changed is None else str(func_changed)
        r["p8_same_sha_pass"] = "" if gold_pass is None else str(gold_pass)
        r["p8_reason"] = reason
        counts[(sub, conf)] += 1

    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print(f"P8 drill-down over {len(rows)} runs from {src}")
    print("cross-PR flaky PRs (suite fails, 0 .go touched):", e2e_untouched_prs)
    print("by subclass:", dict(collections.Counter(r["p8_subclass"] for r in rows)))
    for (s, c), n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {s:8} {c:4} : {n}")
    print("wrote", out)


if __name__ == "__main__":
    main()
