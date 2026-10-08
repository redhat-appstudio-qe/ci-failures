<div dir="rtl">

# ניתוח כשלי CI — מבנה הפרויקט

סיווג **אוטומטי** של כל כשל ב-GitHub Actions לקטגוריית שורש אחת, כדי לענות על שאלה אחת:
**כשה-CI נכשל — מה באמת גרם לזה, ומי אמור לתקן?** הפרויקט רץ על 4 ריפויים של הצוות
(מנוהלי-MintMaker) ועל 2 ריפויי CNCF חיצוניים בוגרים (ESO, Kyverno) כקבוצת-בקרה.

> התוצאות, המסקנות, התהליך וחוקי-הסיווג המלאים — ב-**[RESULTS.md](RESULTS.md)**.
> המסמך הזה עוסק **רק** במבנה: אילו קבצים יש, מה כל אחד עושה, ואיך מריצים.

---

## מבנה הפרויקט

<div dir="ltr">

```
ci_failure_experiment/
│
├── Code/ — "המוח", רץ על כל הריפויים
│   ├── classify.py          Stage-1: שגיאה → קטגוריה (P1..P8 / W1..W8). מקור-אמת יחיד
│   ├── p8_subclassify.py    Stage-2: כשלי-טסט (P8) → Real / Flaky / Unknown
│   ├── pipeline.py          המנוע: python3 Code/pipeline.py <key>
│   └── repos.py             קונפיג per-repo + author_type(). פה מוסיפים ריפו
│
├── README.md                הקובץ הזה — מבנה ותפעול
├── RESULTS.md               תוצאות, מסקנות, תהליך, חוקי-סיווג
│
├── DATA/                    נתונים — תיקייה אחת לכל ריפו (אותו מבנה, תוכן שונה)
│   ├── MPC/                 team (redhat-appstudio/infra-deployments)
│   ├── MAY/                 team (konflux-ci/may)
│   ├── ETCD_SHIELD/         team (konflux-ci/etcd-shield)
│   ├── NAMESPACE_LISTER/    team (konflux-ci/namespace-lister)
│   ├── ESO/                 benchmark (external-secrets/external-secrets)
│   └── KYVERNO/             benchmark (kyverno/kyverno)
│       │
│       │   כל תיקיית-ריפו מכילה:
│       ├── <pre>_runs_raw.tsv                קלט  — הריצות הכושלות לסיווג
│       ├── <pre>_dataset_all.csv             פלט  — כל ריצה מסווגת  ← מתחילים פה
│       ├── <pre>_diverse.csv                 פלט  — בלי כפילויות-חתימה
│       ├── <pre>_dataset_all_p8_subclass.csv פלט  — (MAY, NL בלבד) Stage-2
│       └── cache/ , cache_ann/               gitignored, נבנה בריצה, מתחדש לבד
│
├── Tree.png                 דיאגרמת עץ-ההחלטות של המסווג (מוצג ב-RESULTS)
│
└── verify/                  הוכחת-אמינות: ביקורת ידנית "עיוורת" מול הלוגים
    ├── SPEC.md · reconcile.py
    ├── blind_*.txt          הסיווג הידני של הבודק
    ├── result_*.tsv         ההשוואה מול המסווג
    └── review_corpus.{csv,json}
```

</div>

---

## שלושת רבדי הפרויקט

1. **קוד (`Code/`)** — משותף, לא נכפל per-repo. שינוי ב-`classify.py` משפיע על כולם.
2. **נתונים (6 תיקיות)** — נפרד לכל ריפו. **מבנה כמעט-זהה** (אותם קבצים; הבדלי-עמודות קטנים
   לפי מתי נאסף הריפו — למשל `signal_source`/`tag` קיימים רק בריפויים שנאספו מאוחר), אבל
   כל `*_dataset_all.csv` מחזיק ריפו אחר — **זו לא כפילות, זו הפרדה.**
3. **אימות (`verify/`)** — העדות שהמסווג צודק (ביקורת ידנית עיוורת).

---

## זרימת הנתונים

<div dir="ltr">

```
<pre>_runs_raw.tsv  →  pipeline.py  →  [classify.py → p8_subclassify.py]  →  <pre>_dataset_all.csv / _diverse.csv
                          ↑
                       repos.py (קונפיג per-repo)
```

</div>

`pipeline.py` קורא את ה-TSV, מעשיר כל ריצה דרך ה-GitHub API, מחלץ את שורת-השגיאה מהלוג
(עם נפילה-לאחור ל-check-run annotations כשהלוג פג), מסווג עם `classify.py`, וכותב את ה-CSV-ים.

---

## דרישות מוקדמות

- \u200F**Python 3** — ספריית-תקן בלבד, אין `pip install`.
- \u200F**GitHub CLI** `gh`, מחובר: `gh auth status`. הפייפליין קורא ל-`gh api` ול-`gh run view --log-failed`.

---

## איך מריצים

<div dir="ltr">

```bash
python3 Code/pipeline.py --list          # רשימת הריפויים המוגדרים
python3 Code/pipeline.py kyverno         # סיווג מחדש של ריפו אחד → כותב את קבצי הפלט שלו
```

</div>

ריצות נשמרות ב-cache תחת `DATA/<REPO>/cache`, כך שהרצה חוזרת מיידית ועובדת גם לא-מקוון.
מחיקת ה-`cache/` של ריפו מאלצת משיכה מחדש מ-GitHub.

**עמודות הפלט המרכזיות** ב-`<pre>_dataset_all.csv`: `category` (P1..P8 / W1..W8 / NO_LOG / …),
`cat_label`, `fixable_by`, `author_type` (MintMaker / Other-Bot / Human לריפויי הצוות;
Renovate/Dependabot(upstream) / Bot(other) / Human לבנצ'מרק), `causing_step`, `raw_error`,
`failure_signature`, `signal_source` (live / annotation / ריק=אין), `run_url`.

---

## איך מוסיפים ריפו חדש

1. הוסיפו רשומה ל-[repos.py](Code/repos.py) (`repo`, `datadir`, `pre`, `profile`, `wf_tokens`).
2. צרו `<datadir>/<pre>_runs_raw.tsv` (ראו למטה).
3. \u200F`python3 Code/pipeline.py <key>`.

### איסוף ה-TSV (הקלט)

ל-TSV יש 4 עמודות מופרדות-טאב: `workflowName  databaseId  createdAt  url`. אוספים מדגם
דטרמיניסטי ופרוש-בזמן של ריצות **כושלות** עם pagination של `gh api` (סינון על `created`
עוקף את תקרת 1000 התוצאות של ה-API):

<div dir="ltr">

```bash
REPO=kyverno/kyverno
gh api -X GET "repos/$REPO/actions/runs" -f status=failure -f per_page=100 --paginate \
  --jq '.workflow_runs[] | [.name, .id, .created_at, .html_url] | @tsv' \
  > DATA/KYVERNO/kyverno_runs_raw.tsv
# אם צריך, דוגמים-למטה לפי stride ל-~600 שורות פרושות על פני החלון.
```

</div>

---

## כללי-עבודה (לשמור)

- \u200F**`classify.py` הוא מקור-האמת היחיד.** מנוע אחד מאוחד, בלי fork per-repo. אחרי כל שינוי בו —
  הריצו מחדש את כל הריפויים מה-cache וודאו שההתפלגויות יציבות (בדיקת no-regression).
  \u200F`python3 Code/pipeline.py kyverno` הוא ה-smoke-test המהיר.
- **מעדיפים "לא ניתן לקבוע" על ניחוש.** \u200F`NO_LOG` / `UNCLASSIFIED` הן תשובות כנות.
- **כל המספרים במסמכים** נגזרים מהדאטה-סטים שבתיקיות הריפויים.
- **ארטיפקטים נגזרים** (`_diverse.csv`, `_p8_subclass.csv`) נבנים מ-`_dataset_all.csv`. אם
  מחוללים מחדש רק את ה-`_dataset_all.csv`, הם עלולים לפגר אחריו — מסונכרנים רק אחרי הרצה מלאה.

</div>
