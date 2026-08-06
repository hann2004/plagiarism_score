# Integrity Observatory

Cohort-wide similarity checking on top of JPlag. Compares code and reports
across a batch, keeps every cohort's results, and shows flagged pairs as
a cluster graph or a sortable table.

## What's real vs. what's next

**Working and tested, on real JPlag output, not sample data:**
- Backend runs the actual jar against uploaded submissions
- Results are parsed from JPlag's real output format (topComparisons.json
  plus per-pair match detail)
- Multi-cohort storage, one JSON file per batch, an index tying cohorts
  and their weeks together
- Full frontend flow: cohorts (cluster view) → a cohort's weeks → a
  week's results (cluster or table) → drawer with matched-line detail
- Status tracking per pair (Pending / Confirmed / Cleared), saved to disk

**Deliberately not built yet, per what we discussed:**
- Fetching submissions from GitHub or Google Doc/Drive links. Right now
  you upload a zip. The link-fetching pieces are a separate next step,
  not needed for this stage.
- Any auth/login, this assumes trusted local or internal use for now.

## Setup

1. **Get Java.** Confirm with `java -version`. Any recent JDK works.

2. **Get the real jplag.jar.** Download the `*-jar-with-dependencies.jar`
   build from `https://github.com/jplag/JPlag/releases` and place it at:
   ```
   backend/jplag/jplag.jar
   ```
   (I didn't include it in this zip, it's 79MB and you want the current
   release anyway, not a copy that might already be out of date.)

3. **Install backend dependencies:**
   ```
   cd backend
   pip install -r requirements.txt
   ```

4. **Run it:**
   ```
   uvicorn main:app --reload --port 8000
   ```
   This serves both the API and the frontend. Open `http://localhost:8000`
   in a browser.

## Using it

Click **"Run a comparison"** top right. You'll need:
- A cohort name (e.g. "KAIM Batch 7") and a week/batch name (e.g. "Week 5")
- Whether you're checking code or reports
  - Code uses JPlag's normal language modes
  - Reports use JPlag's `text` mode, which **only reads plain `.txt`
    files**, this was tested and confirmed, not assumed. If reports come
    in as `.docx` or PDF, convert them to `.txt` first.
- A `.zip` file with **one folder per student** inside it

Run code and reports separately for the same cohort/week if you want
both, results merge together rather than overwriting each other.

## Sample data included

`backend/data/` has 7 sample cohorts pre-loaded so the cluster view isn't
empty on first run. Only `batch7` / `week5` has real JPlag results behind
it (the same 3-file test we ran together, amanuel_t vs chala_g scoring
100%, getachew_w scoring 0% against both). The other 6 cohorts are
realistic placeholder numbers so you can see what the multi-cohort view
looks like at real scale, delete `backend/data/*.json` and
`backend/data/index.json` if you want to start from a clean slate.

## Known rough edges, worth knowing before you demo this

- The zip-upload assumes one folder per student already exists inside
  the zip. If your boss's test file is flat (no per-student folders),
  tell me and I'll adjust the unzip logic to detect student boundaries
  differently.
- No handling yet for a submission that fails to parse (e.g. a student
  folder with no valid code files in it), JPlag will just skip it
  silently right now. Worth deciding how you want that surfaced.
- Cross-batch/cross-cohort matching (checking this week against past
  weeks automatically) isn't wired in yet, `crossBatch` exists as a
  field but nothing sets it to `true` yet.
- CORS is wide open (`allow_origins=["*"]`), fine for local dev, tighten
  this before it's ever reachable from outside your own machine.

## Files

```
backend/
  main.py           FastAPI app, all the routes
  jplag_runner.py    Calls the real jar, parses its output
  storage.py         Simple JSON-file storage, one file per batch
  requirements.txt
  jplag/              put jplag.jar here
  data/               sample + real results live here

frontend/
  index.html          everything: markup, styles, and the D3 cluster views
  d3.min.js           D3, vendored so this works with zero build step
```
