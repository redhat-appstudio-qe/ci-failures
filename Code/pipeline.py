#!/usr/bin/env python3
"""
Unified CI-failure pipeline (replaces the old per-repo *_analyze.py scripts).

For one repo it: reads <pre>_runs_raw.tsv (failed runs), enriches each run via the
GitHub API, extracts the causing error line from the live failed-log (with an
annotation fallback when logs have expired), classifies it with the shared
engine in classify.py, and writes <pre>_dataset_all.csv / _diverse.csv.

Usage:
    python3 pipeline.py <repo-key>        # e.g. eso, kyverno, mpc, may, etcd, nl
    python3 pipeline.py --list            # list configured repos

Requirements: Python 3 stdlib only + the `gh` CLI, authenticated (`gh auth status`).
Per-run API responses + logs are CACHED under <datadir>/cache so re-runs are instant.

To collect the input TSV for a repo, see README.md ("Collecting the raw runs").
"""
import json, subprocess, re, csv, sys, os, collections
CODE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(CODE_DIR)            # project root (DATA/ lives here, next to Code/)
sys.path.insert(0, CODE_DIR)
from classify import classify, LABEL, STAGE, fixable_by   # shared Stage-1 engine
from repos import REPOS, author_type                       # per-repo config

# ---------- CLI ----------
args = sys.argv[1:]
if not args or args[0] in ("-h", "--help"):
    print(__doc__); sys.exit(0)
if args[0] == "--list":
    for k, c in REPOS.items():
        print(f"  {k:10} {c['repo']:40} profile={c['profile']} datadir={c['datadir']}")
    sys.exit(0)
KEY = args[0]
if KEY not in REPOS:
    sys.exit(f"unknown repo-key '{KEY}'. Known: {', '.join(REPOS)}  (or --list)")
CFG = REPOS[KEY]
REPO, PRE, PROFILE, WF_TOKENS = CFG["repo"], CFG["pre"], CFG["profile"], CFG["wf_tokens"]
DIR = os.path.join(ROOT, CFG["datadir"])
SRC = os.path.join(DIR, f"{PRE}_runs_raw.tsv")
CACHE = os.path.join(DIR, "cache"); ANN = os.path.join(DIR, "cache_ann")
os.makedirs(CACHE, exist_ok=True); os.makedirs(ANN, exist_ok=True)
if not os.path.exists(SRC):
    sys.exit(f"missing input {SRC} (see README: 'Collecting the raw runs')")

def sh(a): return subprocess.run(a, capture_output=True, text=True, errors="ignore").stdout
def api(p):
    o = sh(["gh", "api", p])
    try: return json.loads(o) if o.strip() else None
    except Exception: return None

# ---- regexes (extraction) ----
WRAP = re.compile(r"process completed with exit code|^make(\[\d+\])?: \*\*\*|^make(\[\d+\])?: leaving|"
                  r"error \d+$|exit status \d+$|^error: failed after|FAIL\s+github|ginkgo ran \d+|"
                  r"test suite failed|there were failures detected", re.I)
CMD_ECHO = re.compile(r"^\[command\]| \\$|^\s*[A-Z][A-Z0-9_]{2,}=|^\s*(?:KUBECONFIG|GOTOOLCHAIN|ENABLE_COVERAGE):|^\s*\+ |^kwokctl |^go (test|build|run) |^cp |^echo ", re.I)
NOISE = re.compile(r"\[new branch\]|-> origin|^remote:|^hint:|deprecat|cleaning up orphan|removing credentials|"
                   r"git config|##\[group\]|##\[endgroup\]|current runner version|^to github|everything up-to-date|"
                   r"^\s*\*\s|##\[warning\]|cache not found for input keys|cache restored|post job cleanup|"
                   r"saving cache|go: downloading|^go: found|^\s*\d+\s+passed|Ran \d+ of|"
                   r"restore cache failed|dependencies file is not found|path validation error|"
                   r"no cache is being saved|never fail the job over a diagnostic", re.I)
STRONG = re.compile(r"\.go:\d+:\d+:|does not implement|impossible type assertion|undefined:|declared and not used|"
                    r"cannot use .* as |no required module|cannot find module|unknown revision|missing go\.sum|"
                    r"go\.mod requires go|requires go >=|"
                    r"could not import|could not resolve|no such file or directory|--- fail|panic:|kubeconfig|no cluster|"
                    r"localhost:8080|could not find a ready|connection refused|dial tcp|manifest unknown|"
                    r"pull access denied|rate limit|no space left|out of memory|command not found|"
                    r"cannot trigger a 'workflow_dispatch'|could not create workflow dispatch", re.I)
SIGNAL = re.compile(r"error|fail|fatal|refused|denied|forbidden|invalid|cannot|not found|conflict|timeout|panic|"
                    r"unable|no such|does not implement|undefined|expected", re.I)

def strip_line(raw):
    p = raw.split("\t"); job = p[0].strip() if len(p) >= 3 else ""; msg = p[-1]
    msg = re.sub(r"^\ufeff?\d{4}-\d{2}-\d{2}T[\d:.]+Z\s*", "", msg).strip()  # drop ts, KEEP ##[error]
    return job, msg

def extract(log, failed_job):
    if not log: return ""
    parsed = [strip_line(l) for l in log.splitlines() if l.strip()]
    def jm(j):
        if not failed_job: return True
        a, b = j.lower(), failed_job.lower()
        return bool(a) and (a == b or a in b or b in a)
    scoped = [m for (j, m) in parsed if jm(j)]
    pool = scoped if scoped else [m for (_, m) in parsed]
    def clean(m): return re.sub(r"##\[error\]", "", m).strip()
    errmark = [clean(m) for m in pool if "##[error]" in m]
    for c in errmark:                       # 1) real error-marked compile/dep/test line
        if c and STRONG.search(c) and not WRAP.search(c) and not CMD_ECHO.search(c) and not NOISE.search(c):
            return c[:220]
    for m in pool:                          # 2) any STRONG line (e.g. golangci output)
        c = clean(m)
        if c and STRONG.search(c) and not WRAP.search(c) and not CMD_ECHO.search(c) and not NOISE.search(c):
            return c[:220]
    for c in errmark:                       # 3) weaker error-marked signal line
        if c and len(c) > 8 and SIGNAL.search(c) and not WRAP.search(c) and not CMD_ECHO.search(c) and not NOISE.search(c):
            return c[:220]
    cleaned = [clean(m) for m in pool]      # 4) WRAP-anchored backward scan
    cleaned = [c for c in cleaned if c and not NOISE.search(c) and not CMD_ECHO.search(c)]
    w = [i for i, c in enumerate(cleaned) if WRAP.search(c)]
    if w:
        for j in range(w[-1] - 1, max(-1, w[-1] - 80), -1):
            if cleaned[j] and len(cleaned[j]) > 8 and SIGNAL.search(cleaned[j]) and not WRAP.search(cleaned[j]):
                return cleaned[j][:220]
    for c in reversed(cleaned):             # 5) last signal line
        if SIGNAL.search(c) and not WRAP.search(c): return c[:220]
    return ""

REPORT = re.compile(r"report|post |upload|cleanup|notify|comment|status|slack|annotat|complete job", re.I)
def pick_steps(job):
    failed = [s.get("name", "") for s in job.get("steps", []) if s.get("conclusion") == "failure"]
    causing = next((s for s in failed if not REPORT.search(s)), failed[0] if failed else "")
    reporting = next((s for s in failed if REPORT.search(s)), "")
    return causing, reporting

def norm_sig(err):
    if not err: return "<none>"
    le = err.lower()
    pats = [(r"does not implement.*missing method|impossible type assertion", "interface mismatch (dep conflict)"),
      (r"module declares its path|but was required as", "module-path mismatch (dep rename)"),
      (r"could not import.*(controller-runtime|k8s\.io|sigs\.k8s\.io)", "dep typecheck fail (controller-runtime/k8s)"),
      (r"no required module|cannot find module|missing go\.sum|unknown revision", "unresolved go dependency"),
      (r"go\.mod requires go|requires go >=|gotoolchain", "Go version mismatch"),
      (r"undefined:|declared and not used|syntax error|expected .* found", "go compile error (source)"),
      (r"golangci-lint|gofmt|not formatted|issues found|sa\d{4}", "golangci-lint / staticcheck"),
      (r"trailing-spaces|key-duplicates|yamllint|\[line-length\]", "yamllint"),
      (r"workflow_dispatch|workflow dispatch event", "dispatch disabled-workflow (W2)"),
      (r"--- fail|test failed|ginkgo|panic:", "test assertion failure"),
      (r"kubeconfig|no cluster|localhost:8080|could not find a ready|\bkind\b|failed calling webhook", "KIND / cluster not ready"),
      (r"manifest unknown|pull access denied|unauthorized|forbidden", "registry/permission error"),
      (r"econnrefused|dial tcp|i/o timeout|connection refused|no such host|unexpected eof|docker pull failed", "network error"),
      (r"rate limit|toomanyrequests", "rate limit"),
      (r"no space left|oomkill|out of memory|acquired by runner", "resource/runner limit"),]
    for p, n in pats:
        if re.search(p, le): return n
    s = re.sub(r"/home/runner/work/_temp/[\w.\-]+", "<TMP>", err)
    s = re.sub(r"[0-9a-f]{8}-[0-9a-f-]{20,}", "<UUID>", s)
    s = re.sub(r"pull/\d+/merge", "pull/<PR>/merge", s); s = re.sub(r"\bv?\d+(\.\d+)+\b", "<VER>", s)
    s = re.sub(r"[0-9a-f]{7,40}", "<HASH>", s); s = re.sub(r"/[\w.\-/]+", "<PATH>", s); s = re.sub(r"\d+", "<N>", s)
    return re.sub(r"\s+", " ", s).strip().lower()[:80]

def test_subreason(c, err):
    if c not in ("P8", "W4"): return ""
    e = err.lower()
    if re.search(r"kubeconfig|no cluster|localhost:8080|could not find a ready|\bkind\b|failed calling webhook", e): return "no-KIND / cluster not ready"
    if re.search(r"--- fail|assert|ginkgo|expected|test failed|panic:", e): return "real test assertion"
    return "test-stage, cause unclear"

# ---- annotation recovery (cached per sha) ----
WRAP_ANN = re.compile(r"process completed with exit code|^exit with code \d+\s*$|^\s*$", re.I)
def check_runs_for(sha):
    cf = os.path.join(ANN, f"{sha}.json")
    if os.path.exists(cf):
        try: return json.load(open(cf))
        except Exception: pass
    out = []
    cr = api(f"/repos/{REPO}/commits/{sha}/check-runs?per_page=100")
    for c in (cr or {}).get("check_runs", []):
        if c.get("conclusion") not in ("failure", "cancelled", "timed_out"): continue
        ann = api(f"/repos/{REPO}/check-runs/{c['id']}/annotations")
        msgs = [[a.get("annotation_level"), (a.get("message") or "").strip().replace("\n", " ")]
                for a in (ann or []) if (a.get("message") or "").strip()]
        out.append([c.get("name", ""), c.get("conclusion"), msgs])
    json.dump(out, open(cf, "w")); return out

def recover(sha, workflow, failed_job=""):
    crs = check_runs_for(sha)
    if not crs: return "", "no"
    for name, concl, msgs in crs:           # run-wide concurrency cancel is authoritative
        for lvl, m in msgs:
            if re.search(r"canceling since a higher priority|canceling since a deployment", m, re.I):
                return m[:220], "cancelled"
    def usable(m): return bool(m) and not WRAP_ANN.search(m) and not NOISE.search(m)
    def best_from(subset):
        for name, concl, msgs in subset:
            for lvl, m in msgs:
                if lvl == "failure" and usable(m): return m[:220]
        for name, concl, msgs in subset:
            for lvl, m in msgs:
                if usable(m): return m[:220]
        return ""
    # TRUTHFUL per-job attribution: trust only the failed job's own check-run(s).
    fj = (failed_job or "").lower()
    jobmatch = [c for c in crs if fj and ((c[0] or "").lower() in fj or fj in (c[0] or "").lower())]
    if jobmatch:
        pick = best_from(jobmatch)
        return (pick, "yes") if pick else ("", "no")
    toks = WF_TOKENS.get((workflow or "").lower(), [])
    matched = [c for c in crs if any(t in (c[0] or "").lower() for t in toks)] if toks else []
    pick = best_from(matched) or best_from(crs)
    return (pick, "yes") if pick else ("", "no")

# ---- fetch bundle (cached): run meta + jobs + failed-log ----
def fetch_bundle(rid, tag):
    cf = os.path.join(CACHE, f"{rid}.json")
    if os.path.exists(cf):
        try: return json.load(open(cf))
        except Exception: pass
    run = api(f"/repos/{REPO}/actions/runs/{rid}")
    if not run:
        b = {"missing": True, "wf": tag, "sha": "", "actor": "", "pr": "", "fj": "", "causing": "", "reporting": "", "log": ""}
        json.dump(b, open(cf, "w")); return b
    jd = api(f"/repos/{REPO}/actions/runs/{rid}/jobs"); jobs = (jd or {}).get("jobs", [])
    fj = causing = reporting = ""
    for j in jobs:
        if j.get("conclusion") == "failure":
            fj = j.get("name", ""); causing, reporting = pick_steps(j); break
    log = sh(["gh", "run", "view", str(rid), "--repo", REPO, "--log-failed"]) if fj else ""
    prs = run.get("pull_requests") or []
    b = {"wf": run.get("name", "") or tag, "sha": run.get("head_sha") or "",
         "actor": (run.get("triggering_actor") or run.get("actor") or {}).get("login", ""),
         "pr": prs[0]["number"] if prs else "", "fj": fj, "causing": causing, "reporting": reporting, "log": log}
    json.dump(b, open(cf, "w")); return b

# ---- main ----
runs = []
for line in open(SRC):
    p = line.rstrip("\n").split("\t")
    if len(p) >= 4: runs.append((p[0], p[1], p[2], p[3]))
print(f"[{KEY}] total raw {len(runs)}", file=sys.stderr)

rows = []; actor_map = {}
n_live = n_ann = n_cancel = n_none = 0
for n, (tag, rid, ts, url) in enumerate(runs, 1):
    if n % 40 == 0: print(f"...{n}/{len(runs)}  live={n_live} ann={n_ann} cancel={n_cancel} none={n_none}", file=sys.stderr)
    b = fetch_bundle(rid, tag)
    wf, sha, actor = b["wf"], b["sha"], b["actor"]
    at = author_type(actor, PROFILE); actor_map[actor] = at
    err = extract(b.get("log", ""), b.get("fj", ""))
    src = "live" if err else ""
    if err:
        n_live += 1
    elif sha:
        err, rec = recover(sha, wf, b.get("fj", ""))
        if rec == "cancelled": src = "annotation"; n_cancel += 1
        elif rec == "yes": src = "annotation"; n_ann += 1
        else: n_none += 1
    else:
        n_none += 1
    c = classify(b.get("causing", ""), err)
    rows.append({"tag": tag, "workflow": wf, "run_url": url, "run_id": rid, "ts": ts, "actor": actor,
      "author_type": at, "PR": b.get("pr", ""), "commit": sha[:10], "failed_job": b.get("fj", ""),
      "causing_step": b.get("causing", ""), "reporting_step": b.get("reporting", ""),
      "category": c, "cat_label": LABEL[c],
      "group": "Project" if c[0] == "P" else ("Workflow" if c[0] == "W" else "-"),
      "stage": STAGE[c], "fixable_by": fixable_by(c), "test_subreason": test_subreason(c, err),
      "raw_error": err, "failure_signature": norm_sig(err), "flaky_signal": "", "signal_source": src})

# NOTE: _diverse.csv (and the separately-generated _p8_subclass.csv) are DERIVED from the
# rows below. If _dataset_all.csv is ever regenerated on its own, those derived files can lag
# until this pipeline (and p8_subclassify.py) are re-run — they are not guaranteed in lockstep.
seen = set(); diverse = []
for r in sorted(rows, key=lambda x: (x["raw_error"] == "",)):
    if r["failure_signature"] in seen: continue
    seen.add(r["failure_signature"]); diverse.append(r)

with open(os.path.join(DIR, f"{PRE}_dataset_all.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
with open(os.path.join(DIR, f"{PRE}_diverse.csv"), "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(diverse[0].keys())); w.writeheader(); w.writerows(diverse)
print(f"DONE all={len(rows)} uniq_sig={len(seen)} live={n_live} ann={n_ann} cancel={n_cancel} none={n_none}", file=sys.stderr)
print("dist:", dict(collections.Counter(r["category"] for r in rows).most_common()), file=sys.stderr)
