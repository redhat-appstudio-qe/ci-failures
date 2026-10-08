# CI Failure Analysis — Project Structure

**Automatic** classification of every GitHub Actions failure into a single root
category, to answer one question: **when CI fails — what actually caused it, and
who should fix it?** The project runs over 4 team repos (MintMaker-managed) and 2
mature external CNCF repos (ESO, Kyverno) as a control group.

> The full results, conclusions, process, and classification rules are in
> **[RESULTS.md](RESULTS.md)**.
> This document covers **only** the structure: which files exist, what each does,
> and how to run it.

---

## Project Structure

```
ci-failures/
│
├── Code/ — "the brain", runs across all repos
│   ├── classify.py          Stage-1: error → category (P1..P8 / W1..W8). Single source of truth
│   ├── p8_subclassify.py    Stage-2: test failures (P8) → Real / Flaky / Unknown
│   ├── pipeline.py          The engine: python3 Code/pipeline.py <key>
│   └── repos.py             Per-repo config + author_type(). Add a repo here
│
├── README.md                This file — structure & operation
├── RESULTS.md               Results, conclusions, process, classification rules
│
├── DATA/                    Data — one directory per repo (same structure, different content)
│   ├── MPC/                 team (redhat-appstudio/infra-deployments)
│   ├── MAY/                 team (konflux-ci/may)
│   ├── ETCD_SHIELD/         team (konflux-ci/etcd-shield)
│   ├── NAMESPACE_LISTER/    team (konflux-ci/namespace-lister)
│   ├── ESO/                 benchmark (external-secrets/external-secrets)
│   └── KYVERNO/             benchmark (kyverno/kyverno)
│       │
│       │   Each repo directory contains:
│       ├── <pre>_runs_raw.tsv                input   — the failed runs to classify
│       ├── <pre>_dataset_all.csv             output  — every run classified  ← start here
│       ├── <pre>_diverse.csv                 output  — without signature duplicates
│       ├── <pre>_dataset_all_p8_subclass.csv output  — (MAY, NL only) Stage-2
│       └── cache/ , cache_ann/               gitignored, built on run, self-refreshing
│
├── Tree.png                 Classifier decision-tree diagram (shown in RESULTS)
│
└── verify/                  Reliability proof: manual "blind" audit against the logs
    ├── SPEC.md · reconcile.py
    ├── blind_*.txt          The reviewer's manual classification
    ├── result_*.tsv         The comparison against the classifier
    └── review_corpus.{csv,json}
```

---

## The Three Project Layers

1. **Code (`Code/`)** — shared, not duplicated per-repo. A change in `classify.py` affects all of them.
2. **Data (6 directories)** — separate per repo. **Near-identical structure** (same files; small
   column differences depending on when the repo was collected — e.g., `signal_source`/`tag` exist
   only in repos collected later), but each `*_dataset_all.csv` holds a different repo —
   **this is not duplication, it's separation.**
3. **Validation (`verify/`)** — the evidence that the classifier is right (manual blind audit).

---

## Data Flow

```
<pre>_runs_raw.tsv  →  pipeline.py  →  [classify.py → p8_subclassify.py]  →  <pre>_dataset_all.csv / _diverse.csv
                          ↑
                       repos.py (per-repo config)
```

`pipeline.py` reads the TSV, enriches each run via the GitHub API, extracts the error line from the
log (with a fallback to check-run annotations when the log has expired), classifies it with
`classify.py`, and writes the CSVs.

---

## Prerequisites

- **Python 3** — standard library only, no `pip install`.
- **GitHub CLI** `gh`, authenticated: `gh auth status`. The pipeline calls `gh api` and `gh run view --log-failed`.

---

## How to Run

```bash
python3 Code/pipeline.py --list          # list the configured repos
python3 Code/pipeline.py kyverno         # re-classify one repo → writes its output files
```

Runs are cached under `DATA/<REPO>/cache`, so a re-run is instant and works offline too.
Deleting a repo's `cache/` forces a re-fetch from GitHub.

**Key output columns** in `<pre>_dataset_all.csv`: `category` (P1..P8 / W1..W8 / NO_LOG / …),
`cat_label`, `fixable_by`, `author_type` (MintMaker / Other-Bot / Human for team repos;
Renovate/Dependabot(upstream) / Bot(other) / Human for the benchmark), `causing_step`, `raw_error`,
`failure_signature`, `signal_source` (live / annotation / empty=none), `run_url`.

---

## How to Add a New Repo

1. Add an entry to [repos.py](Code/repos.py) (`repo`, `datadir`, `pre`, `profile`, `wf_tokens`).
2. Create `<datadir>/<pre>_runs_raw.tsv` (see below).
3. `python3 Code/pipeline.py <key>`.

### Collecting the TSV (the input)

The TSV has 4 tab-separated columns: `workflowName  databaseId  createdAt  url`. Collect a
deterministic, time-spread sample of **failed** runs with `gh api` pagination (filtering on
`created` bypasses the API's 1000-result cap):

```bash
REPO=kyverno/kyverno
gh api -X GET "repos/$REPO/actions/runs" -f status=failure -f per_page=100 --paginate \
  --jq '.workflow_runs[] | [.name, .id, .created_at, .html_url] | @tsv' \
  > DATA/KYVERNO/kyverno_runs_raw.tsv
# If needed, down-sample by stride to ~600 rows spread across the window.
```

---

## Working Rules (keep)

- **`classify.py` is the single source of truth.** One unified engine, no per-repo fork. After every
  change to it — re-run all repos from cache and confirm the distributions are stable (no-regression check).
  `python3 Code/pipeline.py kyverno` is the quick smoke-test.
- **Prefer "cannot determine" over guessing.** `NO_LOG` / `UNCLASSIFIED` are honest answers.
- **All numbers in the docs** are derived from the datasets in the repo directories.
- **Derived artifacts** (`_diverse.csv`, `_p8_subclass.csv`) are built from `_dataset_all.csv`. If you
  regenerate only `_dataset_all.csv`, they may lag behind it — they're in sync only after a full run.
