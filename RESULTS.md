<div dir="rtl">

# ניתוח כשלי CI — תהליך, שיטה, תוצאות ומסקנות

סיכום מלא ועצמאי של הפרויקט: מה עשינו, איך המסווג עובד, מה הקטגוריות, מה התוצאות, ומה
המסקנות. מי שלוקח את הפרויקט יכול לקרוא רק את המסמך הזה ולהבין הכול כמו מי שפיתח.
לתפעול הקוד והמבנה — ראו [README.md](README.md).

**מקור-אמת למספרים:** הדאטה-סטים בתיקיות הריפויים.

---

## מקורות המחקר (בסיס להחלטות הסיווג)

- **טקסונומיה:** Zheng et al., *Why Do GitHub Actions Workflows Fail?*, ACM TOSEM 2025 — [DOI](https://dl.acm.org/doi/10.1145/3749371) (W8 = 6/375 = 1.6%).
- **flaky ללא re-run:** An et al., *Just-in-Time Flaky Test Detection via Abstracted Failure Symptom Matching*, ICSME 2024 — [arXiv](https://arxiv.org/abs/2310.06298) (≥96% precision); DeFlaker, ICSE 2018 — [PDF](https://www.cs.cornell.edu/~legunsen/pubs/BellETAL18DeFlaker.pdf) (diff-reachability).
- **confidence תלת-ערכי:** Meta *Probabilistic Flakiness Score* — [link](https://engineering.fb.com/2020/12/10/developer-tools/probabilistic-flakiness/); iDFlakies, ICST 2019 (unknown-bucket).
- **base-rate:** Micco, *Flaky Tests at Google*, 2016 — [link](https://testing.googleblog.com/2016/05/flaky-tests-at-google-and-how-we.html) (84% מ-Pass→Fail הם flaky).

---

## 1. המטרה

לענות על שאלה מעשית אחת: **כשה-CI שלנו נכשל — מה באמת גרם לזה, ומי אמור לתקן?**
כדי לענות בנינו שלושה דברים:

1. **מסווג אוטומטי** ([classify.py](Code/classify.py)) שמקבל כשל CI גולמי ומחזיר **קטגוריית-שורש אחת** מתוך טקסונומיה קבועה.
2. **פייפליין** ([pipeline.py](Code/pipeline.py)) שאוסף כל כשל מ-GitHub Actions, מחלץ את שורת-השגיאה, ומריץ את המסווג על כולם.
3. **קבוצת-בקרה (Benchmark)** — שני ריפויי CNCF חיצוניים בוגרים (ESO, Kyverno) שעליהם הרצנו את **אותו מסווג בדיוק**, כדי להכריע אם הפרופיל שלנו הוא "רעש-שגרה נורמלי" או חריג שמסמן בעיה אמיתית.

הטקסונומיה לקוחה ממאמר אקדמי: *"Why Do GitHub Actions Workflows Fail?"* (Zheng et al., ACM TOSEM 2025) — 16 קטגוריות: **P1–P8** ברובד הפרויקט, **W1–W8** ברובד ה-workflow.

---

## 2. התהליך — שלבי הניסוי

1. **איסוף** — משיכת הריצות הכושלות מ-workflows נבחרים בכל ריפו, עם תיוג לפי workflow.
2. **העשרה** — לכל ריצה: actor, PR, commit, job כושל, causing-step מול reporting-step (`gh api .../jobs`).
3. **חילוץ אות** — הודעת השגיאה מהלוג של ה-job הכושל (`gh run view --log-failed`), עם דילוג על עוטפים גנריים.
4. **שחזור** — כשהלוג פג (410 Gone): שחזור אות מ-check-run **annotations** (העלה את כיסוי-האות ב-etcd מ-14% ל-95%).
5. **סיווג** — עץ-החלטה "שכבה-קודם" ל-16 הקטגוריות, מצליב TYPE + STEP + ACTOR + תוכן-הלוג.
6. **דדופ** — נרמול signature (הסרת paths/UUIDs/versions) → מציאת הכשלים הייחודיים.
7. **ולידציה ידנית** — מעבר ידני על כל signature מול הלוג **החי**; כשה-`raw_error` השמור לא היה שורת-השורש — חילוץ-מחדש מהלוג או שחזור מ-annotations, כולל היסק חוצה-workflow.
8. **דיווח** — טבלאות לפי קטגוריה / שכבה / actor / fixable-by + זיהוי flaky.

**הדאטה שנאסף — ריצות CI כושלות לכל ריפו:**

| ריפו | סוג | ריצות כושלות |
|---|---|---|
| MPC | צוות | 400 |
| MAY | צוות | 248 |
| etcd-shield | צוות | 406 |
| namespace-lister | צוות | 396 |
| ESO | בקרה | 600 |
| Kyverno | בקרה | 600 |

נאסף לכל כשל (שורה אחת ב-`<pre>_dataset_all.csv`):
`workflow` · `actor`→`author_type` · `PR` · `commit` · `failed_job` · `causing_step` מול `reporting_step` · `raw_error` · `failure_signature` · `category` · `stage` · `fixable_by` · `signal_source` (live / annotation / ריק).
> הבדלי-סכמה קטנים בין הריפויים: `signal_source` (ו-`tag`) קיימים רק בריפויים שנאספו מאוחר (nl / ESO / Kyverno); ב-MPC/MAY/ETCD הם אינם.

---

## 3. מבנה כשל ב-CI, ומה המסווג מקבל כקלט

מבנה ריצה ב-GitHub: `Run` → מכיל → `Jobs` → מכילים → `Steps`.

כשריצה נכשלת, הפייפליין מחלץ **רק שני שדות — וזה כל מה שהמסווג רואה:**

| שדה | מה זה | דוגמה |
|---|---|---|
| `causing_step` | שם ה-**step שגרם** לכשל (לא ה-step המדווח) | `Run golangci-lint` |
| `raw_error` | **שורת-השגיאה** עצמה, מהלוג | `client.go:26:1: File is not properly formatted (gci)` |

**הבחנה מרכזית: causing-step מול reporting-step.** הכשל ה"אמיתי" קורה ב-step אחד (compile/test),
אבל לפעמים step מאוחר (upload/notify/comment) הוא זה ש"צובע" את הריצה באדום. הפייפליין מפריד
ולוקח את ה**גורם**, לא את ה**מדווח**.

**מהיכן מגיעה שורת-השגיאה (לפי עדיפות):**
1. **לוג חי** — `gh run view --log-failed`, בהעדפת שורות `##[error]` אמיתיות על פני הדי-פקודות ואזהרות שפירות.
2. **שחזור מ-annotations** — אם הלוג פג (GitHub מוחק לוגים אחרי ~90 יום), שליפת הודעות ה-annotation של ה-check-run.
3. **אם אין כלום** → הכשל מסומן `NO_LOG` (אין אות לסיווג).

---

## 4. חוקי המסווג

המסווג הוא **עץ-החלטה יחיד, repo-agnostic**, הבנוי על עיקרון אחד:

> **"first match wins"** — עוברים על החוקים מלמעלה למטה ועוצרים בהתאמה הראשונה.

הסדר אינו שרירותי: **שורש-ספציפי נבדק לפני סימפטום-גנרי.** כלומר: infra → network → dependency
→ compile → quality → tests, וסימפטום גנרי לעולם לא "גונב" שורש ספציפי יותר.

![עץ סיווג כשלי CI](Tree.png)

הכלל: `"test"` / `"lint"` בשם ה-step הם **מוצא-אחרון** — נבדקים רק אם אף דפוס-תוכן ספציפי לא התאים.

**שתי הבחנות שנוספו לבקשת QE:**
1. **רשת לפי יעד, לא לפי מילת-מפתח:** `connection refused` הוא *סימפטום*. תלות שבורה/לא-זמינה → **P3**; יעד תקין שזמנית לא-נגיש → **W3**.
2. **W4 = תשתית מול מוצר:** אם KIND/kubeconfig/webhook/envtest נשברו → **W4**. אבל אם ה-**מוצר** (controller/deployment) קרס בתוך תשתית בריאה → זו קטגוריית **P** (אחרת כל E2E שבו המוצר לא עולה היה הופך בטעות ל"בעיית infra").

---

## 5. הקטגוריות ותתי-הקטגוריות

**16 הקטגוריות (+ מותאמות ל-QE), ומי מתקן כל אחת:**

| קוד | שם / הסבר | רובד | מי מתקן |
|---|---|---|---|
| **P1** | קוד שלא עובר הידור — משתנה/import שלא בשימוש, שגיאת תחביר | Build | אדם / build-owner |
| **P2** | הגדרות פרויקט לא תואמות סביבה — גרסת Go ב-`go.mod`/toolchain גבוהה מה-runner | Build | אדם / build-owner |
| **P3** | לא ניתן להשיג תלות — מודול חסר, גרסה לא קיימת, cachi2 נכשל | Build | **MintMaker (dep bump)** |
| **P4** | תלות הובאה אך שבורה מול הקוד — bump שינה API/interface | Build | **MintMaker (dep bump)** |
| **P5** | בדיקות איכות נכשלו — golangci-lint / gofmt / yamllint | Quality | אדם / build-owner |
| **P6** | סריקת אבטחה מצאה חולשה ידועה (CVE) בתלות | Security | אדם / build-owner |
| **P7** | בדיקת ביצועים / benchmark נכשלה | Perf | אדם / build-owner |
| **P8** | הקוד עלה ורץ אך טסט נכשל (assertion) — מפוצל לתתי-P8 למטה | Test | אדם / build-owner |
| **W1** | מגבלת משאבים ב-runner — דיסק/זיכרון מלאים / התהליך נהרג | Infra | Infra/Env (retry) |
| **W2** | קובץ ה-workflow שגוי — פקודה לא קיימת, YAML לא תקין | Config | מתחזק workflow |
| **W3** | כשל רשת **חולף** — connection-refused/timeout/stream-error | Network | Infra/Env (retry) |
| **W4** | תשתית-הטסט לא עלתה — KIND/kubeconfig/envtest/setup-action | Setup/Env | Infra/Env (retry) |
| **W5** | אין הרשאה לצד-ג' — pull מ-registry / credentials / 403 | Perm | Infra/Env (retry) |
| **W6** | Action חיצוני לא נפתר — שם/גרסה שגויים | Config | מתחזק workflow |
| **W7** | חריגה ממכסת ה-API של GitHub (rate limit) | Infra | Infra/Env (retry) |
| **W8** | version-control **בתוך** ריצה — duplicate tag / merge בתוך job (נדיר) | VCS | מתחזק workflow/VCS |
| **W_BUILD_VET** | `build`/`go vet` נכשלו אך שורת-השגיאה לא נלכדה בלוג | Build | אדם / build-owner |
| **NO_LOG** | אין אות שמיש — הלוג פג (410) **או** שהריצה לא יצרה job כלל (למשל fork-PR startup) | — | — |
| **CANCELLED** | בוטל ע"י concurrency — **אינו כשל אמיתי** | — | — |
| **UNCLASSIFIED** | לא סווג (בד"כ פער-חילוץ) | — | — |

> **קבוצת-העל `Infra Setup`** = W4+W5+W6 — שכבת-דיווח וניתוב-retry מעל העלים; העלים נשמרים לתאימות-לאחור מול המאמר ול-drill-down.

### תת-סיווג (Stage 2) — רק ל-P8 (כשלי-טסט)

כש-Stage-1 מחזיר **P8**, הריצה עוברת ל-[p8_subclassify.py](Code/p8_subclassify.py) שמכריע **Real / Flaky / Unknown**, לפי עדיפות:
1. **קריסה דטרמיניסטית** (`panic:` / `[PANICKED!]` / duplicate-descriptor) → **Real / high**.
2. **same-SHA rerun-to-pass** — בדיוק אותו commit עבר בריצה אחרת באותו workflow → **Flaky / high** (gold standard).
3. **diff-reachability** [DeFlaker, ICSE 2018] — הפונקציה שנפלה **לא** שונתה ב-PR (נגעו רק ב-deps/Docker/workflow) → **Flaky / med**; אם שונתה → **Real / med**.
4. **cross-PR + שונות-משך** — אותו טסט נופל על ≥2 PRs לא-קשורים בלי שינוי-קוד → **Flaky**.
5. **אין ראיות מספיקות** → **Unknown / low** (תשובה לגיטימית, לא חולשה).

> כל היוריסטיקות ה-flaky לקוחות ממאמרים בלבד — לא הומצאו. ראו מקורות בסוף.

### איך הופרד author_type (ו-CANCELLED)

סוג-היוצר נקבע מ-**`actor` login** של הריצה (מי דחף/פתח את ה-PR), לא מתוכן הלוג:
- **MintMaker** — `red-hat-konflux[bot]` / `renovate` / `mintmaker` (בוט עדכוני-התלות של Konflux).
- **Other-Bot / Automation** — כל login של בוט (`…[bot]` / `-bot` / `dependabot` / `kargo`) שאינו MintMaker.
- **Human** — כל login שאינו בוט.

> ⚠️ **הגבלה מודעת:** הפילוח בפועל הוא `Human / MintMaker / Other-Bot`, **לא** `Agentic-AI`. login של בוט מעיד על **automation**, לא על כך שסוכן-AI כתב את ה-PR — אין דרך להוכיח authorship של agent מה-actor login בלבד.
>
> \u200F**CANCELLED** אינו סוג-יוצר אלא **קטגוריית-כשל** — `Canceling since a higher priority...` מסמן שריצה חדשה דחקה ישנה (concurrency), כלומר אין כאן כשל אמיתי.
>
> **אימות actor מול PR-author:** נבדק על 17 PRs ייחודיים (`gh api /pulls/{n}.user.login` מול ה-actor) → **100% התאמה** (17/17). ה-workflows כאן מופעלים ב-`pull_request`, ולכן ה-actor אכן משקף את יוצר ה-PR.

---

## 6. התוצאות — ריפויי הצוות

### טבלה A — קטגוריות ושכבות (% מהכשלים האמיתיים; ריצות בסוגריים)

| קוד / שכבה | MPC | MAY | etcd-shield | namespace-lister |
|---|---|---|---|---|
| **P8 · Test (E2E/perf)** | — | **48%** (84) | — | **8%** (29) ⟨perf⟩ |
| **P8 · Test (Unit)** | — | 1% (2) | — | — |
| P4 · Build | **67%** (163) | 13% (22) | 24% (79) | **46%** (161) |
| P3 · Build | 3% (8) | **29%** (50) | **70%** (230) | 9% (31) |
| P2 · Build | — | — | 5% (17) | 2% (7) |
| P1 · Build | — | 4% (7) | — | — |
| P5 · Lint/Quality | — | — | 0.3% (1) | 6% (21) |
| **W2 · Workflow config** | — | — | — | **22%** (76) |
| W4 · Setup/Env *(Infra Setup)* | 15% (37) | 1% (2) | — | 5% (17) |
| W5 · 3rd-party perm *(Infra Setup)* | — | 1% (1) | — | — |
| W6 · Action unresolvable *(Infra Setup)* | — | 1% (2) | — | — |
| W3 · Network | 2% (6) | 3% (5) | 0.3% (1) | 3% (9) |
| W1 · Resource · W7 · API-rate | — | — | — | 0.3%·0.3% (1·1) |
| W_BUILD_VET · Build | 13% (31) | — | 0.3% (1) | — |
| **שכבה: Build** | **82%** | 45% | **99%** | **56%** |
| **שכבה: Test** | 0% | **49%** | 0% | 8% |
| **קבוצת-על: Infra Setup** | **15%** | **3%** | **0%** | **5%** |

> ⟨perf⟩ 29 ה-P8 של nl הם **כולם** קריסת-`[PANICKED!]` דטרמיניסטית בחבילת-perf (`performance_test.go:112`). בשונה מ-MAY (P8 ברובו E2E-flaky), כאן P8 הוא **Real** לחלוטין.

### טבלה B — היקף, איכות-דאטה, וסוג-יוצר (מכל הריצות)

| מדד | MPC | MAY | etcd-shield | namespace-lister |
|---|---|---|---|---|
| ריצות כושלות | 400 | 248 | 406 | 396 |
| CANCELLED | 0 | 0 | 44 (10%) | 21 (5%) |
| signatures ייחודיים | 11 | 16 | 18 | 26 |
| **MintMaker** | **377 (94%)** | 193 (77%) | **384 (94%)** | 295 (74%)† |
| **Human** | 16 (4%) | 54 (21%) | 22 (5%) | 16 (4%) |
| **Other-Bot** (לא Agentic-AI) | 7 (2%) | 1 (0%) | 0 (0%) | **85 (21%)**† |

### תת-סיווג P8 (Stage-2) — MAY מול namespace-lister: פרופיל הפוך

הפיצול להלן הוא פלט מסווג-המשנה (drill-down אמיתי), לא ידני ולא regex.

**MAY — 86 כשלי P8:**

| תת-סיווג | ריצות | confidence | נימוק |
|---|---|---|---|
| **P8-F Flaky** | 2 | **high** | gold-standard: אותו `E2E Tests` הצליח על אותו SHA בדיוק (same-SHA rerun-to-pass) |
| **P8-F Flaky** | 63 | med | diff-reachability: E2E נפל ב-PRs שנגעו רק ב-deps/Docker/workflow — 0 קוד `.go` + שונות-משך גבוהה |
| **P8-R Real** | 3 | **high** | `panic: previously registered descriptor` — crash דטרמיניסטי משוחזר |
| **P8-R Real** | 14 | med | PR 192 ("wire AWS host reconcilers") ששינה `.go`/`*_test.go` → הכשל נגזר מהשינוי |
| **P8-U Unknown** | 4 | low | הפונקציה שנפלה לא שונתה באף diff — ראיות לא-מספיקות |

**namespace-lister — 29 כשלי P8:**

| תת-סיווג | ריצות | confidence | נימוק |
|---|---|---|---|
| **P8-R Real** | 29 | **high** | קריסת-`[PANICKED!]` דטרמיניסטית ב-`performance_test.go:112` — זהה ב-100% מהריצות, חוצה 5 PRs, 0 שינוי `.go`, שונות-משך ≈0. bump-תלות שבר את ה-authorizer ב-runtime |

> **ניגוד מאלף:** ב-MAY **65/86 (76%) Flaky**; ב-nl **29/29 (100%) Real דטרמיניסטי**. המסקנה: **"P8 גבוה" כשלעצמו אינו מעיד על flakiness** — חייבים את מסווג-המשנה (Stage-2) כדי להבחין בין טסט רועד לבין רגרסיית-מוצר אמיתית שנדחפה ע"י בוט.

### ממצא מתודולוגי מרכזי: פער-חילוץ שיטתי (Systemic Extraction Gap)

בוולידציה הידנית התגלה ששני ה-buckets הגדולים ביותר היו **מקובצים-שגוי**: ה-`raw_error`
השמור **לא היה שורת-השורש** אלא הד-פקודה / אזהרה שפירה / סימפטום במורד-הזרם:
- **MAY — 51 שורות שסווגו W4** (המפתח היה הד-הפקודה `cp $HOME/.kube/config`): בפועל **50× P3** (cel-go שינה module-path, שובר `go mod` ב-bump של MintMaker) + **1× W3**. ⟶ W4 קרס 53→2, P3 קפץ 0→50.
- **etcd — 19 שורות שסווגו P5** (המפתח היה `issues found`, שורת-סיכום של golangci-lint): בפועל **16× P4** + **2× P3** + **1× P5 אמיתי**. ⟶ P5 קרס 19→1, P4 עלה 63→79.
- **namespace-lister — 10 שורות שסווגו P8 הוחזרו ל-W4** (6× `deployment exceeded its progress deadline` + 4× `before scenario hook failed`) — כשל-**סביבה**, לא assertion.

> **הלקח למסווג:** דדופ לפי `raw_error` גולמי מסתיר הטרוגניות כשה-raw_error עצמו שגוי — **חובה לחלץ את שורת-השורש לפני דדופ**.
>
> **"הסכמה-עיוורת ≠ אמת":** הוולידציה-העיוורת (agent נפרד שקרא את אותו `raw_error`) **הסכימה עם התוויות השגויות** — ולכן שיעור ה-reconcile ירד 95%→91.5% *דווקא בגלל* התיקון הידני. הסכמה-עיוורת מודדת **שחזוריות** של חוק, לא **נכונות** — רק חילוץ-מחדש מהלוג חשף את האמת.

### פערי-דאטה — 3 קטגוריות עם 0 דוגמאות

מתוך 16 קטגוריות המאמר, **3 עדיין 0 דוגמאות** בכל ארבעת הריפויים: **P6** (CVE — אין gate של govulncheck/Trivy
ב-workflows שנדגמו), **P7** (benchmark — אין gate), **W8** (version-control בתוך ריצה — נדיר מיסודו: במאמר
1.6%, רובו pre-CI; 0 בכל הריפויים הוא **צפוי, לא באג**). namespace-lister **מילא 3 פערים קודמים** —
W1 (runner לא הוקצה), W2 (dispatch ל-workflow מושבת), W7 (dependabot חטף `toomanyrequests`) — כולם דוגמאות
חיות אמיתיות. היוותרות P6/P7/W8 חסרים היא הסיבה ש**עדיין אי-אפשר לאמן מסווג ML קלאסי** — אין דגימות חיוביות ללמוד מהן.

---

## 7. קבוצת הבקרה — ESO + Kyverno

הרצנו את **אותו מסווג מאוחד** (עם ולידציה מול live-log/annotation) על 600 הכשלים האחרונים (90 יום)
של **ESO** (external-secrets) ושל **Kyverno** (kyverno/kyverno) — שני ריפויי CNCF בוגרים שאינם
מנוהלי-MintMaker. הפרדנו שכבת **fork-PR startup** (ריצות 0-job שנכשלות ב-startup — מנגנון merge-ref
של GitHub, **לא כשל-קוד**) לפי כלל אחיד: ריצה בלי job-data = startup.

| שכבה | ESO | Kyverno |
|---|---|---|
| (a) **fork-PR startup** (0 jobs, לא כשל-קוד) | 490 (**82%**) | 280 (**47%**) |
| (b) **כשלים אמיתיים ברמת-job** (השכבה ההשוואתית) | 110 (18%) | 319 (53%) |

**היפוך-פרופיל מובהק מול הצוות (רמת-job):**

| ממד | ריפויי הצוות | ESO | Kyverno |
|---|---|---|---|
| נכתב ע"י בוט-תשתית (MintMaker) | **74%–94%** | **0%** | **0%** |
| תלות (P3+P4) | **41%–93%** | **~3%** | 20% (תערובת אדם/Dependabot) |
| נכתב ע"י בני-אדם | 4%–21% | **86%** | **62%** |
| מצב-הכשל הדומיננטי | bump-תלות אוטומטי שבור | fork-PR startup + איכות אנושית | פיתוח אנושי (compile/test) + fork-startup |

**הניואנס ב-Kyverno:** שני ה"בוטים" שלו (`kyverno-pr-updater[bot]`, `kyverno-bot`) הם אוטומציה
פנימית, לא בוט-תלויות; ה-P4 שלו הוא תערובת של שינויי-API אנושיים (`not enough arguments in call
to buildWebhook`) ו-Dependabot קהילתי, **לא מפעל-bumps**. המצב הדומיננטי שלו הוא פיתוח אנושי:
P1 קומפילציה (12%), P5 lint, P8 טסטים — כולם נתפסים לפני merge.

> **הערת-כנות:** לשתי הבנצ'מרקים יש שיעור `UNCLASSIFIED` גבוה בשכבה (b) (~73% ESO, ~48% Kyverno) בגלל פער-החילוץ. אך האות הכמותי הנקי — `author_type` ושיעורי ה-MintMaker/תלות — חד-משמעי.

---

## 8. מסקנות

1. **דומיננטיות עדכוני-התלות אצלנו היא תוצר של שיטת-הניהול, לא של "CI ב-Go".** אותו מסווג בדיוק, על **שני** ריפויי Go בוגרים שאינם מנוהלי-MintMaker, מראה פרופיל **הפוך**: 0% MintMaker ו-~3%/20% תלות מול 74%–94% MintMaker ו-41%–93% תלות אצלנו. המשתנה המבחין הוא **מי דוחף קוד** (בוט MintMaker מול בני-אדם), לא השפה ולא ה-CI.
2. **זו גם בשורה טובה.** רוב הכשלים אצלנו אינם באגים בקוד או ב-CI — הם bumps אוטומטיים שבורים, **צפויים וניתנים-לתיקון-ע"י-בוט** (P3/P4 = MintMaker-fixable). ה-CI עושה את עבודתו: תופס שבירות-תלות לפני merge.
3. **לכל ריפו "חתימה" משלו** — אין "כשל CI" יחיד: MPC/nl נשלטים ע"י P4, etcd ע"י P3, MAY ע"י P8, ESO ע"י fork-startup, Kyverno ע"י compile/test אנושי. המסווג מאפשר לנהל כל ריפו לפי הפרופיל שלו.
4. **"כשל CI" אינו אחיד, ו-"P8 גבוה" ≠ flaky** — חייבים מסווג רב-ריפו + מסווג-משנה Stage-2 (MAY 76% flaky מול nl 100% Real).
5. **ולידציה ידנית הכרחית** — קריאת `raw_error` גולמי לבדה טעתה בכל ריפו, לעתים בקנה-מידה. חובה לחלץ שורת-שורש לפני דדופ.
6. **Data-Quality:** לוגים פגים אחרי 90 יום (410 Gone) אך **annotations שורדות** — חובה **איסוף יומי רציף**. (הערה: `NO_LOG` מאחד שני מצבים — לוג שפג, *וגם* ריצה ללא job כלל כמו fork-PR startup בבנצ'מרקים; ראו §7.)

---

## מתודולוגיית-שקיפות

כל טענה אומתה מול לוג/annotation חי. העדפנו "לא ניתן לקבוע" (`NO_LOG`/`UNCLASSIFIED`) על ניחוש.
המסווג מאוחד על פני כל הריפויים (בלי fork per-repo), וכל שינוי בו עובר בדיקת no-regression על כל
הדאטה-סטים. עדות האימות: [verify/](verify/).

</div>
