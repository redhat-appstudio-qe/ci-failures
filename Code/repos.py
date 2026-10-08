"""
Per-repo configuration for the unified CI-failure pipeline.

One entry per repository. To onboard a NEW repo, add an entry here and run:
    python3 pipeline.py <key>

Fields:
  repo       "owner/name" on GitHub.
  datadir    sub-directory (relative to this file) holding that repo's data
             (<pre>_runs_raw.tsv input + <pre>_*.csv/json outputs + cache/).
  pre        filename prefix for that repo's artifacts.
  profile    "konflux" | "upstream"  -> selects how author logins are labelled
             (see author_type below). Team repos are MintMaker-managed ("konflux");
             the benchmark repos are community upstream.
  wf_tokens  workflow-name -> keywords, used ONLY as a lower-confidence fallback for
             annotation recovery when the failed job's own check-run can't be matched.
             Safe to leave {} (falls back to run-wide recovery).
"""

REPOS = {
    # ---- team repos (MintMaker-managed) ----
    "mpc": dict(
        repo="redhat-appstudio/infra-deployments", datadir="DATA/MPC", pre="mpc",
        profile="konflux", wf_tokens={}),
    "may": dict(
        repo="konflux-ci/may", datadir="DATA/MAY", pre="may",
        profile="konflux", wf_tokens={}),
    "etcd": dict(
        repo="konflux-ci/etcd-shield", datadir="DATA/ETCD_SHIELD", pre="etcd",
        profile="konflux", wf_tokens={}),
    "nl": dict(
        repo="konflux-ci/namespace-lister", datadir="DATA/NAMESPACE_LISTER", pre="nl",
        profile="konflux", wf_tokens={
            "lint checks": ["lint"], "lint renovate": ["lint", "renovate"],
            "fullsend": ["fullsend", "test", "e2e", "integration", "send", "dispatch"],
            "dependency impact analysis": ["dependency", "impact", "triage"],
            "dependabot updates": ["dependabot"], "codeql": ["codeql"]}),

    # ---- benchmark repos (upstream CNCF, NOT MintMaker-managed) ----
    "eso": dict(
        repo="external-secrets/external-secrets", datadir="DATA/ESO", pre="eso",
        profile="upstream", wf_tokens={
            "ci": ["lint", "test", "build", "fmt", "vet"],
            "e2e tests": ["e2e", "test", "integration"], "managed e2e tests": ["e2e", "test"],
            "crds": ["crd", "generate", "diff"], "helm": ["helm", "lint", "test", "chart"],
            "codeql advanced": ["codeql", "analyze"], "zizmor actions scanner": ["zizmor", "scan"],
            "dependency review": ["dependency", "review"], "dependency license checks": ["license", "dependency"],
            "dependabot updates": ["dependabot"], "update dependencies": ["renovate", "update"],
            "ok to test (review)": ["test", "e2e"], "pull request maintenance": ["maintenance", "stale"]}),
    "kyverno": dict(
        repo="kyverno/kyverno", datadir="DATA/KYVERNO", pre="kyverno",
        profile="upstream", wf_tokens={
            "unit tests": ["unit", "test"], "tests": ["test"], "conformance tests": ["conformance", "test", "e2e"],
            "framework tests (envtest)": ["framework", "envtest", "test"], "cli": ["cli", "test"],
            "go": ["go", "build", "test"], "go vet": ["vet"], "go format": ["fmt", "format", "gofmt"],
            "go imports": ["import", "goimports"], "codegen": ["codegen", "generate"], "verify codegen": ["codegen", "verify"],
            "lint": ["lint", "golangci"], "load tests": ["load", "test"], "chart-testing": ["chart", "helm", "lint"],
            "build images": ["build", "image", "ko", "docker"], "publish images": ["publish", "image"],
            "trivy (periodic scan)": ["trivy", "scan"], "dependabot updates": ["dependabot"],
            "dependabot merge triage": ["dependabot", "merge"]}),
}


def author_type(login, profile):
    """Label a GitHub actor login. The split is the heart of the benchmark:
    team repos distinguish MintMaker (the Konflux dep-bump bot) from humans;
    upstream repos have community renovate/dependabot, NOT MintMaker."""
    l = (login or "").lower()
    if not l:
        return "Unknown"
    if profile == "upstream":
        if "renovate" in l:
            return "Renovate(upstream)"
        if "dependabot" in l:
            return "Dependabot(upstream)"
        if l.endswith("[bot]") or l.endswith("-bot") or "github-actions" in l:
            return "Bot(other)"
        return "Human"
    # konflux (team) profile
    if "mintmaker" in l or "renovate" in l or l == "red-hat-konflux[bot]":
        return "MintMaker"
    # non-MintMaker automation. Labelled "Other-Bot" (NOT "Agentic"): a bot login proves
    # automation only, not that an AI agent authored the PR — unprovable from the actor login.
    if (l.endswith("[bot]") or l.endswith("-bot") or "kargo" in l
            or "rh-tap-build-team" in l or "dependabot" in l):
        return "Other-Bot"
    return "Human"
