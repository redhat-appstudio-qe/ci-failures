#!/usr/bin/env python3
"""
UNIFIED canonical CI-failure classifier (Stage 1) — one engine for ALL repos.
Supersedes the diverged per-repo reclassify.py scripts (MPC/MAY/etcd), merging the
best rule from each plus precision fixes. Repo-agnostic.

    python3 classify.py <dataset.csv> [--write]     # re-derive category from raw_error+step
                                                     # prints a diff vs the current labels;
                                                     # --write persists the new labels.

Rule ORDER matters (first match wins). The order encodes the decision tree:
infra/among-noise first, then network, then dependency, then compile, then quality,
then tests, so a symptom never steals a more specific root cause.

ARCHITECTURE (how this file fits the project)
---------------------------------------------
This is the "brain" (Stage 1). It has TWO entry points:
  1. classify()  — a PURE function (no file I/O, no network, no loop, no repo awareness).
                   Input = (causing-step name, raw error line); output = one category string.
                   pipeline.py imports it and calls it once per run inside its main loop.
  2. main()      — a standalone CLI tool for RE-classifying an existing dataset CSV in place
                   (reads a file, optionally --write). Used to test rule changes without
                   re-fetching anything from GitHub.
Everything below classify() is either static lookup data or that offline CLI tool.
"""
import re, csv, sys, collections

# ---- Block: static taxonomy tables (pure data, no logic) ----
# LABEL = human-readable name per category; STAGE = the ownership/triage layer per category.
# Both are plain dicts keyed by the category codes classify() returns, used by pipeline.py and
# main() to enrich each row. Changing wording here is cosmetic; it never affects classification.
LABEL = {"P1":"Compile: source code","P2":"Build: artifact/config","P3":"Compile: unresolved deps",
"P4":"Compile: dependency conflict","P5":"Quality checks","P6":"Vulnerabilities","P7":"Performance",
"P8":"Software tests fail","W1":"Resource limitation","W2":"Workflow config/instructions",
"W3":"Network","W4":"Env setup / KIND","W5":"3rd-party permission","W6":"Action unresolvable",
"W7":"API rate limit","W8":"Version control","W_BUILD_VET":"Build/vet fail (error not captured)",
"NO_LOG":"No failure signal (log expired or no job)","CANCELLED":"Concurrency-cancelled (not a real failure)",
"UNCLASSIFIED":"Unclassified"}
STAGE = {"P1":"Build","P2":"Build","P3":"Build","P4":"Build","P5":"Lint/Quality","P6":"Security",
"P7":"Perf","P8":"Test","W1":"Infra","W2":"Workflow-config","W3":"Infra/Network","W4":"Setup/Env",
"W5":"Infra/Perm","W6":"Workflow-config","W7":"Infra","W8":"VCS","W_BUILD_VET":"Build",
"NO_LOG":"Unknown (no signal)","CANCELLED":"Cancelled","UNCLASSIFIED":"Unknown"}


# ============================================================================================
# Block: the decision engine (Stage 1 core).
# PURE function — no file access, no GitHub, no loop. pipeline.py calls it per run.
# It is a single ordered if-chain: the FIRST matching rule wins and returns immediately, so the
# block order below IS the decision tree. Each numbered block narrows from "most specific root"
# to "most generic symptom", so a generic symptom can never steal a more specific root cause.
# ============================================================================================
def classify(step, err):
    """Return the root-cause category for one failure. `step` = causing step name."""
    e = (err or "").lower()
    st = (step or "").lower()
    if not err:
        # no usable signal: the log expired (410) OR the run produced no job to log
        # (e.g. fork-PR startup failures) -> both surface here as NO_LOG.
        return "NO_LOG"

    # ---- [0] non-failures & un-usable signal ----
    # arch: filter out runs that are NOT real code failures before any root-cause rule runs,
    # so they can never be mislabelled downstream (cancel / deprecation-only / empty cache).
    if re.search(r"canceling since a higher priority|canceling since a deployment|the operation was canceled", e):
        return "CANCELLED"
    # only a deprecation WARNING survived; the real error line is gone
    if re.search(r"node\.js \d+ actions are deprecated|actions are deprecated and will", e):
        return "NO_LOG"
    if re.fullmatch(r"cache is not found\.?", e.strip()):
        return "W_BUILD_VET"

    # ---- [1] dependency-obtain failures that LOOK like other things ----
    # arch: pulled up FIRST among real causes because cachi2 prefetch errors otherwise match the
    # generic network rule [4]; catching them here keeps them P3 (a dependency problem).
    # cachi2 hermetic prefetch: dependency could not be obtained -> P3 (before network 5xx rule)
    if re.search(r"cachi2/output|invalid: reading file:///cachi2|version \".*\" invalid", e):
        return "P3"

    # ---- [2] test INFRASTRUCTURE (before network/test rules) ----
    # arch: a webhook unreachable DURING a test is an unhealthy cluster/env, not the test itself;
    # placed before the network[4] and test[13] rules so it isn't mislabelled W3/P8.
    # in-cluster webhook not reachable during a test = cluster/env not healthy, not the test
    if re.search(r"failed calling webhook|failed to call webhook|webhook.*post \"https", e):
        return "W4"

    # ---- [3] external action resolution (before generic 5xx network rule) ----
    # arch: distinguishes a genuinely bad action ref (W6) from a GitHub-side outage while resolving
    # a VALID action (transient -> W3). Must run before the generic network rule to make that call.
    if re.search(r"failed to resolve action|resolve action download|resolving \"actions/|"
                 r"unable to resolve action|can't find.*action|action .* not found", e):
        # a GitHub-side 5xx while resolving a VALID action is a transient infra outage -> W3, not W6
        if re.search(r"internal server error|service unavailable|bad gateway|gateway time|\b50[234]\b", e):
            return "W3"
        return "W6"

    # ---- [3b] acceptance-setup infra: the build/cluster could not even be created ----
    # arch: setup/env failures that happen INSIDE a step named "...tests"; must precede both the
    # network rule [4] and the test rule [13] or they get mislabelled W3 / P8.
    # Must precede the network rule (a kind-create node-image pull failure is an ENV/setup
    # failure, not a standalone network outage) AND the test rule (these happen inside a step
    # named "...tests", so otherwise the 'test'-in-step heuristic mislabels them P8).
    # runner's container runtime (podman/crun) could not create the image-build container:
    if re.search(r"error running container|/usr/bin/crun|while running runtime: exit status", e):
        return "W4"
    # KIND cluster creation failed (e.g. bad node-image reference / node-image pull):
    if re.search(r"failed to create cluster|kind create cluster|kindest/node", e):
        return "W4"

    # ---- [4] transient network / infra on a VALID target ----
    # arch: the generic "connectivity symptom" bucket. Everything above has already claimed the
    # cases where the same symptom actually has a more specific root (dependency / setup / action).
    if re.search(r"connection refused|connection reset by peer|dial tcp|i/o timeout|no such host|"
                 r"could not resolve host|unable to access 'https|econnrefused|port 22", e):
        return "W3"
    if re.search(r"stream error|internal_error|internal server error|unable to download|unable to fetch|"
                 r"got status \"5\d\d|proxy\.golang\.org.*(error|stream)|verifying module.*reading https|"
                 r"reading https.*(error|500|timeout)|bad gateway|service unavailable|gateway time", e):
        return "W3"
    if re.search(r"known_hosts|\.ssh/|load_hostkeys", e):
        return "W3"
    # transient registry/CDN failure while pulling an image (EOF/reconnect mid-blob) = network
    if re.search(r"unable to copy from source docker|writing blob.*(eof|reconnect)|unexpected eof|"
                 r"error pulling image|failed to pull|docker pull failed|read: connection reset", e):
        return "W3"
    # an action that could not fetch its own tool/release version (GitHub API/network) = transient net
    if re.search(r"failed to fetch latest version", e):
        return "W3"

    # ---- [5] in-run version control ----
    # arch: VCS operations failing INSIDE the run (rare; W8). Distinct from dependency resolution.
    if re.search(r"couldn't find remote ref|merge conflict|needs to be rebased|non-fast-forward|"
                 r"tag already exists|! \[rejected\]", e):
        return "W8"

    # ---- [6] rate limit / permissions / resources ----
    # arch: infra-quota / credentials / runner-exhaustion bucket (W7/W5/W1) — environmental, not code.
    if re.search(r"rate limit|toomanyrequests", e):
        return "W7"
    if re.search(r"nocredentials|unable to locate credentials|no credentials|manifest unknown|"
                 r"pull access denied|unauthorized|403 forbidden|denied: requested access", e):
        return "W5"
    # resource/runner exhaustion: OOM, disk, or GitHub failed to provision a hosted runner
    if re.search(r"no space left|out of memory|signal: killed|cannot allocate memory|"
                 r"was not acquired by runner", e):
        return "W1"

    # ---- [7] test-environment setup (envtest binaries / KIND / kubeconfig) ----
    # arch: the test ENVIRONMENT failed to come up (W4). Deliberately precedes the P8 test rule [13]
    # so a cluster/rollout that never got ready is not misread as a test assertion.
    if re.search(r"failed to set up envtest|envtest binaries|setup-envtest|fetch.*envtest", e):
        return "W4"
    if re.search(r"kubeconfig|no cluster|localhost:8080|could not find a ready|\bkind\b|kube/config", e):
        return "W4"
    # k8s deployment/pod not ready during test setup (cert/secret race, volume mount) = env not ready
    if re.search(r"failedmount|mountvolume\.setup failed|secret \".*\" not found", e):
        return "W4"
    # the deployment-under-test never reached Ready (rollout exceeded its progress deadline), or the
    # test harness could not even discover the API server before any scenario ran (BDD before-hook):
    # the test ENVIRONMENT was not ready, NOT a test assertion. Must precede the P8 test rule [13].
    if re.search(r"exceeded its progress deadline|failed to get server groups|before scenario hook", e):
        return "W4"

    # ---- [8] dependency CONFLICT (obtained but breaks the code) ----
    # arch: the dependency WAS fetched but its API no longer fits the code (P4) — a bump broke things.
    # Separated from [9] "could not fetch at all" (P3): different owner signal (both MintMaker-fixable).
    if re.search(r"does not implement|impossible type assertion|incompatible|ambiguous import|"
                 r"not enough arguments in call to|too many arguments in call to", e):
        return "P4"
    if re.search(r"cannot use ", e) and re.search(r"go/pkg/mod|sigs\.k8s\.io|k8s\.io/|controller-runtime|apimachinery", e):
        return "P4"
    if re.search(r"cannot use ", e):
        return "P4"
    # a compile error located INSIDE a dependency (go/pkg/mod/...e.g. a dep's zz_generated.go
    # referencing 'undefined: validate.EachSliceVal') is a dependency CONFLICT -- the pinned dep
    # set is mutually incompatible -- NOT the project's own source (P1). Mirrors 'cannot use' above.
    if re.search(r"undefined:", e) and re.search(r"go/pkg/mod", e):
        return "P4"
    # golangci/compiler could not typecheck a DEPENDENCY package because its API no longer fits
    # the pinned set (cannot use / does not implement) -> dependency CONFLICT.
    # Guard: if the real reason in the parens is "missing go.sum"/unresolved, it is P3 (handled
    # below), not P4 -- so exclude those here.
    if re.search(r"could not import (sigs\.k8s\.io|k8s\.io|controller-runtime|github\.com/|"
                 r"go\.uber\.org|google\.golang\.org|go\.opentelemetry\.io|cel\.dev)", e) \
       and not re.search(r"missing go\.sum|no required module|cannot find module|unknown revision", e):
        return "P4"

    # ---- [9] unresolved dependency ----
    # arch: the dependency could not be OBTAINED at all (P3) — missing module / renamed path / bad
    # version. Checked after [8] so a conflict in a fetched dep isn't swallowed by the broader net.
    # incl. module-path mismatch (a dep renamed its module path, e.g. cel-go
    # github.com/google/cel-go -> cel.dev/cel-go): go mod cannot resolve it -> P3
    if re.search(r"missing go\.sum|no required module|cannot find module|unknown revision|"
                 r"no matching versions|module declares its path|but was required as", e):
        return "P3"

    # ---- [10] genuine source compile (incl. compile errors inside *_test.go) ----
    # arch: only reached once all dependency explanations are ruled out -> the project's OWN code
    # does not compile (P1). Owner = human, not MintMaker.
    if re.search(r"undefined:|declared and not used|imported and not used|syntax error|"
                 r"expected .* found|missing ['\",]+ before|\.go:\d+:\d+:.*(missing|expected|syntax|undefined|declared)", e):
        return "P1"

    # ---- [11] build/packaging & project-vs-toolchain ----
    # arch: config/toolchain mismatch (P2) — e.g. go.mod requires a newer Go than the runner has.
    if re.search(r"devsetup|/out/|cannot find package|build failed|go\.mod requires go|gotoolchain|running go \d", e):
        return "P2"

    # ---- [12] quality / security / perf ----
    # arch: the code compiled; these are gate checks (P5 lint / P6 CVE / P7 perf). Note P5 also
    # triggers on "lint" in the STEP name as a last resort when no content pattern matched.
    if re.search(r"golangci|gofmt|not formatted|yamllint|line-length|trailing-spaces|\bissues found\b", e) or "lint" in st:
        return "P5"
    if re.search(r"vulnerab|cve-|trivy|snyk|govulncheck", e):
        return "P6"
    if re.search(r"benchmark.*(regress|exceed|slower)|performance regression", e):
        return "P7"

    # ---- [13] tests (product built & ran, assertion failed) ----
    # arch: the LAST-RESORT real-failure bucket (P8). The product built and ran, a test assertion
    # failed. "test" in the STEP name is the weakest trigger, deliberately kept last. P8 rows are
    # what the separate Stage-2 script (p8_subclassify.py) later drills into for Real/Flaky/Unknown.
    if re.search(r"--- fail|test failed|ginkgo|panic:|assert", e) or "test" in st:
        return "P8"

    # ---- [14] workflow file itself (bad YAML, missing cmd, or a bad dispatch call) ----
    # arch: the workflow definition is wrong (W2). Last specific rule before giving up.
    if re.search(r"invalid workflow|command not found|exit code 127|"
                 r"could not create workflow dispatch event|cannot trigger a 'workflow_dispatch'|"
                 r"on a disabled workflow", e):
        return "W2"

    # arch: honest fallback — a signal existed but matched no rule (usually an extraction gap).
    return "UNCLASSIFIED"


# ---- Block: ownership mapping (pure lookup) ----
# arch: maps a category to WHO fixes it. Pure function, no I/O. The P3/P4 -> MintMaker mapping is
# the one that drives the project's central finding (bot-pushed dependency bumps dominate).
def fixable_by(c):
    if c in ("P3", "P4"): return "MintMaker-fixable (dep bump)"
    if c in ("P1", "P2", "P5", "P6", "P7", "P8", "W_BUILD_VET"): return "Human / build-owner"
    if c in ("W1", "W3", "W4", "W5", "W7"): return "Infra/Env (retry/ops)"
    if c in ("W2", "W6", "W8"): return "Workflow/VCS maintainer"
    if c == "CANCELLED": return "N/A (not a real failure)"
    return "N/A (no log)"


# ============================================================================================
# Block: standalone offline CLI (NOT used by pipeline.py).
# arch: a re-classification tool. It READS one dataset CSV (another file), re-runs classify() over
# its rows IN A LOOP, prints a diff of changed labels, and with --write saves the new labels back.
# No GitHub calls — it reuses the raw_error/causing_step already stored in the CSV, so it is the
# fast way to test a rule change across every repo without re-fetching anything.
# ============================================================================================
def main():
    if len(sys.argv) < 2:
        sys.exit("usage: classify.py <dataset.csv> [--write]")
    path = sys.argv[1]
    write = "--write" in sys.argv
    rows = list(csv.DictReader(open(path)))
    changed = []
    for r in rows:
        new = classify(r.get("causing_step"), r.get("raw_error"))
        if new != r["category"]:
            changed.append((r.get("raw_error", "")[:80], r["category"], new, r["run_url"]))
        r["category"] = new
        r["cat_label"] = LABEL[new]
        r["group"] = "Project" if new[0] == "P" else ("Workflow" if new[0] == "W" else "-")
        r["stage"] = STAGE[new]
        r["fixable_by"] = fixable_by(new)
    print(f"{path}: {len(rows)} rows, {len(changed)} changed vs current")
    for err, old, new, url in changed:
        print(f"  {old:>12} -> {new:<12} | {err!r} | {url}")
    print("new distribution:", dict(collections.Counter(r["category"] for r in rows).most_common()))
    if write:
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader(); w.writerows(rows)
        print("WROTE", path)


if __name__ == "__main__":
    main()
