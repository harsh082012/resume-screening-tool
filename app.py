"""
app.py
Flask web application for the Resume Screening AI Tool.

Screening now runs OFF the request thread:
  1. POST /screen saves the uploaded files (fast) and starts a background
     thread that scores them with bounded concurrency (see core/runner.py).
     The request returns immediately -- no gunicorn timeout, no hung browser.
  2. The browser is redirected to /results/<run_id>, which shows a live
     progress page (polling /status/<run_id>) while the job runs, then the
     ranked results once it's done.

This decoupling is what makes big batches safe to deploy, and it's the same
model the Slack bot will use (acknowledge instantly, score in the background,
post results when finished).
"""

import os
import io
import csv
import uuid
import shutil
import threading
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, send_file, jsonify
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# IMPORTANT: load .env BEFORE importing any `core` modules -- core.llm_client
# reads env vars at import time, so load_dotenv() must run first or it
# silently falls back to defaults.
load_dotenv()

from core.extractor import extract_text, SUPPORTED_EXTENSIONS
from core.runner import run_screening

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret-key-change-in-production")

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
RESUME_SUBFOLDER = os.path.join(UPLOAD_FOLDER, "resumes")
os.makedirs(RESUME_SUBFOLDER, exist_ok=True)

# Total upload cap. Raised to 100 MB since a "many resumes at once" batch of
# PDFs adds up quickly.
MAX_CONTENT_LENGTH = 100 * 1024 * 1024
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# How many resumes to score concurrently. 3 is safe for OpenRouter free-tier;
# raise via env if you move to a provider with a higher rate limit.
MAX_WORKERS = int(os.getenv("SCREENING_MAX_WORKERS", "3"))

# In-memory store of screening jobs, keyed by run_id. Each job dict tracks
# live status/progress and (when finished) the ranked results.
#
# NOTE: this lives in one process's memory. Run gunicorn with a SINGLE worker
# (and multiple threads) so every request hits the same store -- see Procfile.
# For true multi-worker / multi-instance use, swap this for Redis or a DB.
JOBS = {}


def allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in SUPPORTED_EXTENSIONS


def _run_job(run_id: str, jd_text: str, resume_files: list):
    """Runs in a background thread. Scores the batch and updates JOBS[run_id]."""
    job = JOBS[run_id]
    run_folder = os.path.join(RESUME_SUBFOLDER, run_id)

    def on_progress(done, total, last_file):
        job["done"] = done
        job["total"] = total
        job["last_file"] = last_file

    try:
        ranked, errors = run_screening(
            jd_text,
            resume_files,
            max_workers=MAX_WORKERS,
            on_progress=on_progress,
        )
        job["candidates"] = ranked
        job["errors"] = errors
        if ranked:
            job["status"] = "done"
        else:
            job["status"] = "failed"
            job["error_message"] = (
                "No resumes could be scored. " + " ".join(errors)
            )
    except Exception as e:
        job["status"] = "failed"
        job["error_message"] = f"Screening failed unexpectedly: {e}"
    finally:
        # The uploaded resume files are only needed long enough to read + score.
        # Results live in the in-memory JOBS store (and the CSV is built from
        # there), so once scoring is finished the raw files on disk are dead
        # weight. Delete this run's folder so uploads/ doesn't grow forever --
        # both locally and on the deployed host.
        shutil.rmtree(run_folder, ignore_errors=True)


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/screen", methods=["POST"])
def screen():
    jd_text = (request.form.get("jd_text") or "").strip()
    jd_file = request.files.get("jd_file")

    # JD can come from pasted text OR an uploaded file (incl. an image, via OCR).
    if jd_file and jd_file.filename and allowed_file(jd_file.filename):
        tmp_path = os.path.join(UPLOAD_FOLDER, secure_filename(jd_file.filename))
        jd_file.save(tmp_path)
        try:
            jd_text = extract_text(tmp_path)
        except Exception as e:
            flash(f"Could not read the job description file ({e}).", "error")
            return redirect(url_for("index"))
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    if not jd_text:
        flash("Please paste a job description or upload a JD file.", "error")
        return redirect(url_for("index"))

    resume_files = [f for f in request.files.getlist("resumes") if f and f.filename]
    if not resume_files:
        flash("Please upload at least one resume.", "error")
        return redirect(url_for("index"))

    run_id = str(uuid.uuid4())[:8]
    run_folder = os.path.join(RESUME_SUBFOLDER, run_id)
    os.makedirs(run_folder, exist_ok=True)

    # Save files now (fast); the background thread will read + score them.
    saved = []
    skipped = []
    for f in resume_files:
        filename = secure_filename(f.filename)
        if not allowed_file(filename):
            skipped.append(f"{filename}: unsupported file type, skipped.")
            continue
        filepath = os.path.join(run_folder, filename)
        f.save(filepath)
        saved.append((filepath, filename))

    if not saved:
        flash("None of the uploaded files are supported types. " + " ".join(skipped), "error")
        return redirect(url_for("index"))

    JOBS[run_id] = {
        "status": "running",
        "done": 0,
        "total": len(saved),
        "last_file": None,
        "candidates": [],
        "errors": list(skipped),  # carry forward any pre-skipped files
        "jd_text": jd_text,
        "created_at": datetime.utcnow().isoformat(),
    }

    # daemon=True so the thread doesn't block process shutdown.
    threading.Thread(target=_run_job, args=(run_id, jd_text, saved), daemon=True).start()

    session["last_run_id"] = run_id
    return redirect(url_for("results", run_id=run_id))


@app.route("/status/<run_id>", methods=["GET"])
def status(run_id):
    """JSON progress for the polling progress page."""
    job = JOBS.get(run_id)
    if not job:
        return jsonify({"status": "missing"}), 404
    return jsonify({
        "status": job["status"],
        "done": job["done"],
        "total": job["total"],
        "last_file": job["last_file"],
    })


@app.route("/results/<run_id>", methods=["GET"])
def results(run_id):
    job = JOBS.get(run_id)
    if not job:
        flash("That results set has expired. Please run a new screening.", "error")
        return redirect(url_for("index"))

    if job["status"] == "running":
        return render_template(
            "screening.html",
            run_id=run_id,
            done=job["done"],
            total=job["total"],
        )

    if job["status"] == "failed":
        flash(job.get("error_message", "Screening failed."), "error")
        return redirect(url_for("index"))

    return render_template(
        "results.html",
        run_id=run_id,
        candidates=job["candidates"],
        errors=job["errors"],
        jd_text=job["jd_text"],
    )


@app.route("/download/<run_id>.csv", methods=["GET"])
def download_csv(run_id):
    job = JOBS.get(run_id)
    if not job or job["status"] != "done":
        flash("That results set has expired or isn't ready yet.", "error")
        return redirect(url_for("index"))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rank", "Candidate Name", "Source File", "ATS Score",
        "Skills Match", "Experience Match", "Education Match",
        "Years Experience (est.)", "Matched Skills", "Missing Skills", "Summary",
    ])
    for c in job["candidates"]:
        writer.writerow([
            c.get("rank"),
            c.get("candidate_name"),
            c.get("source_filename"),
            c.get("ats_score"),
            c.get("skills_match_score"),
            c.get("experience_match_score"),
            c.get("education_match_score"),
            c.get("years_experience_estimate"),
            "; ".join(c.get("matched_skills", [])),
            "; ".join(c.get("missing_skills", [])),
            c.get("summary"),
        ])

    mem = io.BytesIO(output.getvalue().encode("utf-8"))
    mem.seek(0)
    return send_file(
        mem,
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"resume_screening_results_{run_id}.csv",
    )


@app.route("/health", methods=["GET"])
def health():
    """Simple health check endpoint, useful once deployed."""
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "true").lower() == "true"
    port = int(os.getenv("PORT", 5000))
    # threaded=True lets the dev server handle /status polls while a job runs.
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)