# ScreeningBench — Resume Screening AI Tool


I built this as a working, end-to-end AI tool that extracts resumes from a
folder (or via upload), compares each one against a job description using an
LLM, generates ATS fit scores with a category breakdown, gives each
candidate specific improvement suggestions, and ranks the pool.

I gave it two ways to use it:
- **Web app** (`app.py`) — an interactive Flask dashboard: paste/upload a JD,
  upload resumes, get a ranked, expandable results screen + CSV export.
- **CLI** (`cli.py`) — a true folder-based batch runner: point it at a folder
  of resumes and a JD file, get a ranked CSV. Good for automation/large batches,
  and satisfies the "extract resumes from a folder" requirement directly.

---

## 1. Project structure

```
resume-screening-tool/
├── app.py                  # Flask web app
├── cli.py                  # Folder-based batch CLI
├── core/
│   ├── extractor.py        # PDF/DOCX/TXT text extraction
│   ├── scorer.py           # Builds the JD-vs-resume prompt, structures output
│   └── llm_client.py       # Provider-agnostic LLM wrapper (OpenRouter/Gemini/Anthropic/Ollama)
├── templates/               # Jinja2 HTML templates
├── static/style.css         # Styling
├── sample_jd.txt            # Demo job description
├── sample_resumes/          # 3 demo resumes (strong/moderate/weak fit)
├── requirements.txt
├── .env.example              # Copy to .env and fill in
├── Procfile / render.yaml   # Deployment config
```

---

## 2. Getting an API key (free)

We don't need a paid Claude subscription for this — the app calls the AI
through an API key, which is separate and billed independently from Claude.ai.

**What I used and confirmed working: OpenRouter**
1. Go to https://openrouter.ai/keys, sign in, choose **Individual**, create a key
2. Skip the "add payment method" prompt (click "I'll do this later") — I didn't
   need it for the free models
3. In `.env`, set:
   ```
   LLM_PROVIDER=openrouter
   OPENROUTER_API_KEY=your_key_here
   OPENROUTER_MODEL=openrouter/free
   ```

   `openrouter/free` is OpenRouter's own **auto-router** — it automatically
   picks whichever free model is currently live and available, instead of
   me hardcoding one specific model name.

   **Why I settled on this:** OpenRouter's free-model catalog rotates
   unpredictably — models get added, rate-limited under load, or moved to
   paid with no warning. While building this, I hit temporary rate limits
   (HTTP 429) on `meta-llama/llama-3.3-70b-instruct:free`, and then found
   `qwen/qwen-2.5-72b-instruct:free` had been fully deprecated to a paid-only
   model mid-build. Pointing at `openrouter/free` instead of a specific model
   name sidestepped this entirely. If you want a specific named model instead,
   check what's currently free at https://openrouter.ai/models (filter by
   price) before hardcoding it.

**Alternative: Google Gemini (free tier)** — I found this inconsistent during
testing (some accounts/regions have had trouble with the free tier). Get a
key at https://aistudio.google.com/apikey if you want to try it; set
`LLM_PROVIDER=gemini`.

**Alternative: Anthropic (Claude) API** — usage-billed, but scoring a resume
costs a fraction of a cent, so a full demo costs well under $1. Get a key at
https://console.anthropic.com and add a small prepaid credit.

**Alternative: Ollama (fully local, fully free, offline)** — no API key at
all. Install from https://ollama.com, run `ollama pull llama3`, then
`ollama serve`. Good for local demos; I couldn't use this once deployed
publicly, since the host would need to run the model itself.

I built it so we can switch between all four anytime via one line in
`.env` — the rest of the code doesn't change.

---

## 3. Setup in VS Code

1. Open the `resume-screening-tool` folder in VS Code.
2. Open a terminal (`` Ctrl+` ``) and create a virtual environment:
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Copy the environment template and fill in your key:
   ```bash
   cp .env.example .env
   ```
   Open `.env`, set `LLM_PROVIDER=openrouter`, and paste your `OPENROUTER_API_KEY`
   (leave `OPENROUTER_MODEL=openrouter/free`).
5. Run the web app:
   ```bash
   python app.py
   ```
   Open **http://localhost:5000** in your browser.

6. I bundled sample data so you can try it immediately: paste the contents of
   `sample_jd.txt` into the JD box, and upload the three files from
   `sample_resumes/`.

### Running the CLI instead
```bash
python cli.py --jd sample_jd.txt --resumes_dir sample_resumes --output outputs/results.csv
```
This prints a ranked table to the console and writes a CSV report.

---

## 4. How the AI evaluation works

For each resume, `core/scorer.py` sends the JD text and resume text to the
configured LLM in a single prompt and asks for a structured JSON response
containing:

- an overall **ATS score (0–100)**
- sub-scores for **skills**, **experience**, and **education** match
- lists of **matched** and **missing** skills/requirements
- an estimated years-of-experience
- a short recruiter-style **summary**
- 3–5 **specific, actionable suggestions** for what the candidate could add
  or rephrase in their resume to better match this particular JD

I then sort candidates by ATS score to produce the final ranking.

Each resume is evaluated independently and directly against the JD text (no
vector database/retrieval step), since both documents comfortably fit in a
single prompt — I kept it this way deliberately, to keep the pipeline simple
and fully deterministic to reason about.

**Reliability:** free-tier models occasionally return HTTP 429 (rate
limited) under load. I built `core/llm_client.py` to automatically retry up
to 3 times, honoring the wait time OpenRouter specifies in its error
response, before giving up on that resume. You'll see `[retry] ...` printed
in the terminal when this happens — it's expected occasional behavior on a
free tier, not a bug.

### Architecture note: no LangChain

I deliberately built this without LangChain or any other orchestration
framework. `llm_client.py` makes plain HTTP calls (via the `requests`
library) straight to whichever provider's REST API is configured. For a
single "send one prompt, parse one JSON response" task per resume, that's
all I needed — LangChain's chains/agents/memory/retrieval abstractions solve
problems (multi-step tool use, conversational memory, vector-store
retrieval) that don't apply here, so adding it would mean an extra
dependency without extra capability. If this project grows to need
multi-step agentic behavior later, LangChain would be a reasonable thing to
introduce then — but the current pipeline doesn't call for it.

---

## 5. Deployment

**How I actually deployed this:** I initially set this up on Render,
connected to a GitHub repository, and got it fully working there — including
tracking down and fixing a Gunicorn timeout bug (the default 30-second
worker timeout was too short for slower AI responses, so I set
`--timeout 120`/`--timeout 300` in the start command).

Partway through, I was asked not to push this project to GitHub. Since
Render requires a linked GitHub, GitLab, or Bitbucket repository and has no
way to deploy from local source directly, I moved the deployment to
**Railway**, which supports deploying straight from my local machine via its
CLI (`railway up`) — no Git repository involved at any point.

**Steps I followed to deploy on Railway:**
1. Installed the Railway CLI and ran `railway login`
2. From inside the project folder, ran `railway init` to create a new project
3. Ran `railway up` to build and deploy directly from local source
4. In the Railway dashboard's **Variables** tab, added the required
   environment variables by hand (`LLM_PROVIDER`, `OPENROUTER_API_KEY`,
   `OPENROUTER_MODEL`, `FLASK_SECRET_KEY`, `FLASK_DEBUG=false`) — Railway
   doesn't read a local `.env` file automatically, so each one has to be
   added directly in its dashboard
5. Along the way, I ran into a port mismatch (a manually-set `PORT` variable
   was overriding Railway's own auto-assigned port, causing a 502
   "Application failed to respond" even though the container itself was
   healthy) — removing that stray `PORT` variable and letting Railway manage
   it resolved it
6. Redeployed with `railway up`, confirmed clean startup in the deploy logs,
   and verified a full screening run end-to-end on the live URL

**Note on cost:** Railway isn't free indefinitely — new accounts get a
one-time $5 trial credit rather than a permanent free tier. That's enough to
run and demo this project comfortably; ongoing use beyond that requires a
paid plan.

### Alternative — Render (if GitHub is permitted)
If a GitHub repository is an option, Render is the simpler path:
1. Push the project to a GitHub repository.
2. Go to https://render.com → New → Blueprint → connect the repo.
   Render reads `render.yaml` automatically and provisions the service.
3. When prompted, paste `OPENROUTER_API_KEY` into the environment variable
   field (marked `sync: false` so it's never committed to git).
4. Deploy. Render gives a live public URL within a few minutes.

   Note: Render's free tier spins the service down after inactivity, so the
   first request after idling takes ~30–60 seconds to wake up.

### General notes for any host
Any host that runs Python + gunicorn works (Railway, Render, Fly.io,
PythonAnywhere, a VPS, etc.):
```bash
pip install -r requirements.txt
gunicorn app:app --bind 0.0.0.0:$PORT --timeout 120
```
Make sure `LLM_PROVIDER=openrouter`, `OPENROUTER_API_KEY` (or the chosen
provider's key), and `FLASK_SECRET_KEY` are set as environment variables on
the host, and don't hardcode a `PORT` value — let the host assign it.

**Important:** the `ollama` provider can't be deployed to a typical free web
host — it needs a machine with the model actually running on it. I used
OpenRouter for anything deployed publicly.

---

## 6. Notes / limitations (worth mentioning in a submission)

- Results are stored in memory per run for simplicity (fine for a
  single-user demo like this one). For multi-user production use, I'd swap
  `RUNS` in `app.py` for a real database.
- Supported resume formats: `.pdf`, `.docx`, `.txt`. Scanned/image-only PDFs
  with no selectable text won't extract text (would need OCR, e.g.
  Tesseract, as a future enhancement).
- Scores are AI-generated estimates meant to assist a human reviewer, not
  replace one — the UI footer says this deliberately.
