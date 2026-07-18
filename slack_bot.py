# """
# slack_bot.py — full screening in Slack, with temp-file cleanup.

# Flow HR uses:
#     1. Paste the job description as a message   -> bot saves it, confirms.
#     2. Drop resume files in the next message     -> bot acknowledges instantly,
#        scores them in the background, posts ranked results + a CSV, then
#        DELETES the downloaded files (they're only needed long enough to read).

# Loads BOTH env files:
#     .env       -> LLM_PROVIDER / OPENROUTER_API_KEY etc (same as the web app)
#     .env.slack -> Slack tokens

# Run:  python slack_bot.py
# """

# import os
# import csv
# import io
# import shutil
# import tempfile
# import threading
# import requests
# from dotenv import load_dotenv

# # Load LLM config first (OpenRouter etc), then the Slack tokens.
# load_dotenv(".env")
# load_dotenv(".env.slack")

# from slack_bolt import App
# from slack_bolt.adapter.socket_mode import SocketModeHandler

# # Reuse the SAME engine the web app uses: extraction (with OCR) + scoring.
# from core.extractor import extract_text, SUPPORTED_EXTENSIONS
# from core.runner import run_screening

# BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]   # xoxb-...
# APP_TOKEN = os.environ["SLACK_APP_TOKEN"]   # xapp-...

# MAX_WORKERS = int(os.getenv("SCREENING_MAX_WORKERS", "3"))

# app = App(token=BOT_TOKEN)

# # Per-user "pending JD" store (the bot has no memory between messages).
# pending_jd = {}


# def download_slack_file(url: str, dest_path: str):
#     """Download a private Slack file using the bot token."""
#     resp = requests.get(
#         url,
#         headers={"Authorization": f"Bearer {BOT_TOKEN}"},
#         timeout=60,
#     )
#     resp.raise_for_status()
#     with open(dest_path, "wb") as f:
#         f.write(resp.content)


# def tier_emoji(score):
#     if score >= 75:
#         return ":large_green_circle:"
#     if score >= 50:
#         return ":large_yellow_circle:"
#     return ":red_circle:"


# def build_results_csv(candidates):
#     output = io.StringIO()
#     writer = csv.writer(output)
#     writer.writerow([
#         "Rank", "Candidate Name", "Source File", "ATS Score",
#         "Skills Match", "Experience Match", "Education Match",
#         "Years Experience (est.)", "Matched Skills", "Missing Skills", "Summary",
#     ])
#     for c in candidates:
#         writer.writerow([
#             c.get("rank"), c.get("candidate_name"), c.get("source_filename"),
#             c.get("ats_score"), c.get("skills_match_score"),
#             c.get("experience_match_score"), c.get("education_match_score"),
#             c.get("years_experience_estimate"),
#             "; ".join(c.get("matched_skills", [])),
#             "; ".join(c.get("missing_skills", [])),
#             c.get("summary"),
#         ])
#     return output.getvalue().encode("utf-8")


# def format_candidate(c):
#     matched = ", ".join(c.get("matched_skills", [])) or "_none identified_"
#     missing = ", ".join(c.get("missing_skills", [])) or "_none — strong coverage_"
#     return "\n".join([
#         f"{tier_emoji(c.get('ats_score', 0))} *#{c.get('rank')}  {c.get('candidate_name', 'Unknown')}*  —  *{c.get('ats_score', 0)}/100*",
#         f"_{c.get('source_filename', '')}_  |  Skills {c.get('skills_match_score', 0)} · "
#         f"Experience {c.get('experience_match_score', 0)} · Education {c.get('education_match_score', 0)} · "
#         f"{c.get('years_experience_estimate', 'N/A')}",
#         f"{c.get('summary', '')}",
#         f"*Matched:* {matched}",
#         f"*Missing:* {missing}",
#     ])


# def run_and_post(client, channel, jd_text, resume_files):
#     """Background thread: download, score, post results, then clean up."""
#     tmp_dir = tempfile.mkdtemp(prefix="slack_screen_")
#     try:
#         saved = []
#         skipped = []

#         for f in resume_files:
#             filename = f.get("name", "unknown")
#             ext = os.path.splitext(filename)[1].lower()
#             if ext not in SUPPORTED_EXTENSIONS:
#                 skipped.append(f"{filename} (unsupported type)")
#                 continue
#             url = f.get("url_private_download") or f.get("url_private")
#             if not url:
#                 skipped.append(f"{filename} (no download link)")
#                 continue
#             dest = os.path.join(tmp_dir, filename)
#             try:
#                 download_slack_file(url, dest)
#                 saved.append((dest, filename))
#             except Exception as e:
#                 skipped.append(f"{filename} (download error: {e})")

#         if not saved:
#             client.chat_postMessage(
#                 channel=channel,
#                 text="Couldn't read any of those files. " + " ".join(skipped),
#             )
#             return

#         ranked, errors = run_screening(jd_text, saved, max_workers=MAX_WORKERS)

#         if not ranked:
#             client.chat_postMessage(
#                 channel=channel,
#                 text="No resumes could be scored. " + " ".join(errors + skipped),
#             )
#             return

#         client.chat_postMessage(channel=channel, text=f":clipboard: *{len(ranked)} candidate(s) ranked*")

#         for c in ranked:
#             client.chat_postMessage(channel=channel, text=format_candidate(c))

#         problems = errors + skipped
#         if problems:
#             client.chat_postMessage(channel=channel, text=":warning: Skipped: " + "; ".join(problems))

#         client.files_upload_v2(
#             channel=channel,
#             filename="resume_screening_results.csv",
#             content=build_results_csv(ranked),
#             title="Ranked results (CSV)",
#             initial_comment="Full ranked results as a spreadsheet:",
#         )
#     finally:
#         # Downloaded resumes are only needed long enough to read + score.
#         # Delete them so disk usage doesn't grow with every screening.
#         shutil.rmtree(tmp_dir, ignore_errors=True)


# @app.event("message")
# def handle_message(event, say, client, logger):
#     if event.get("bot_id"):
#         return

#     user = event.get("user")
#     channel = event.get("channel")
#     files = event.get("files", [])
#     text = (event.get("text") or "").strip()

#     if files:
#         jd_text = pending_jd.get(user)
#         if not jd_text:
#             say("I don't have a job description yet. Please paste the JD text first, then drop the resumes.")
#             return

#         say(f":hourglass_flowing_sand: Screening {len(files)} resume(s) against your JD… I'll post the ranked results here shortly.")

#         threading.Thread(
#             target=run_and_post,
#             args=(client, channel, jd_text, files),
#             daemon=True,
#         ).start()

#         pending_jd.pop(user, None)
#         return

#     if text:
#         pending_jd[user] = text
#         say(
#             f"Got the job description ({len(text)} characters).\n"
#             f"Now drop the resume files here — PDF, DOCX, TXT, or images."
#         )
#         return

#     say("Send me a job description as text first, then drop the resume files.")


# @app.event("app_mention")
# def handle_mention(event, say):
#     say("DM me directly: paste a job description, then drop the resumes.")


# if __name__ == "__main__":
#     print("Starting Slack bot (full screening + cleanup)... (Ctrl+C to stop)")
#     handler = SocketModeHandler(app, APP_TOKEN)
#     handler.start()

"""
slack_bot.py — HTTP-mode Slack bot (for Render / any web host).

This is the HTTP (Events API) version. Instead of holding a websocket open
(Socket Mode), Slack sends events to a public URL on this server:
    POST /slack/events

The screening logic is identical to the Socket Mode version — only the
transport changed. Bolt acknowledges Slack instantly (well within Slack's
3-second rule) and runs the listener in the background, so long screenings
don't cause Slack timeouts.

Local run (dev):   python slack_bot.py
Production (Render): gunicorn slack_bot:flask_app --workers 1 --threads 8 --bind 0.0.0.0:$PORT

Environment variables:
    SLACK_BOT_TOKEN       xoxb-...  (Bot User OAuth Token)
    SLACK_SIGNING_SECRET  from Slack app → Basic Information → App Credentials
    LLM_PROVIDER=openrouter
    OPENROUTER_API_KEY
    OPENROUTER_MODEL=openai/gpt-oss-20b:free
    SCREENING_MAX_WORKERS=3   (optional; defaults to 3)

NOTE: HTTP mode does NOT use SLACK_APP_TOKEN (that was Socket Mode only).
      It uses SLACK_SIGNING_SECRET instead to verify requests are really
      from Slack.
"""

import os
import csv
import io
import shutil
import tempfile
import threading
import requests
from dotenv import load_dotenv

# Load LLM config first (OpenRouter etc), then the Slack secrets.
# On Render these come from the dashboard env vars; locally from the files.
load_dotenv(".env")
load_dotenv(".env.slack")

from flask import Flask, request
from slack_bolt import App
from slack_bolt.adapter.flask import SlackRequestHandler

# Reuse the SAME engine the web app uses: extraction (with OCR) + scoring.
from core.extractor import extract_text, SUPPORTED_EXTENSIONS
from core.runner import run_screening

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]              # xoxb-...
SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]    # from Basic Information

MAX_WORKERS = int(os.getenv("SCREENING_MAX_WORKERS", "3"))

# HTTP mode: App is created with the signing secret (no app-level token).
app = App(token=BOT_TOKEN, signing_secret=SIGNING_SECRET)

# Per-user "pending JD" store (the bot has no memory between messages).
# This lives in memory, so the web server MUST run with a single worker
# (--workers 1) or a JD saved by one worker won't be seen by another.
pending_jd = {}


def download_slack_file(url: str, dest_path: str):
    """Download a private Slack file using the bot token."""
    resp = requests.get(
        url,
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
        timeout=60,
    )
    resp.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(resp.content)


def tier_emoji(score):
    if score >= 75:
        return ":large_green_circle:"
    if score >= 50:
        return ":large_yellow_circle:"
    return ":red_circle:"


def build_results_csv(candidates):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Rank", "Candidate Name", "Source File", "ATS Score",
        "Skills Match", "Experience Match", "Education Match",
        "Years Experience (est.)", "Matched Skills", "Missing Skills", "Summary",
    ])
    for c in candidates:
        writer.writerow([
            c.get("rank"), c.get("candidate_name"), c.get("source_filename"),
            c.get("ats_score"), c.get("skills_match_score"),
            c.get("experience_match_score"), c.get("education_match_score"),
            c.get("years_experience_estimate"),
            "; ".join(c.get("matched_skills", [])),
            "; ".join(c.get("missing_skills", [])),
            c.get("summary"),
        ])
    return output.getvalue().encode("utf-8")


def format_candidate(c):
    matched = ", ".join(c.get("matched_skills", [])) or "_none identified_"
    missing = ", ".join(c.get("missing_skills", [])) or "_none — strong coverage_"
    return "\n".join([
        f"{tier_emoji(c.get('ats_score', 0))} *#{c.get('rank')}  {c.get('candidate_name', 'Unknown')}*  —  *{c.get('ats_score', 0)}/100*",
        f"_{c.get('source_filename', '')}_  |  Skills {c.get('skills_match_score', 0)} · "
        f"Experience {c.get('experience_match_score', 0)} · Education {c.get('education_match_score', 0)} · "
        f"{c.get('years_experience_estimate', 'N/A')}",
        f"{c.get('summary', '')}",
        f"*Matched:* {matched}",
        f"*Missing:* {missing}",
    ])


def run_and_post(client, channel, jd_text, resume_files):
    """Background thread: download, score, post results, then clean up."""
    tmp_dir = tempfile.mkdtemp(prefix="slack_screen_")
    try:
        saved = []
        skipped = []

        for f in resume_files:
            filename = f.get("name", "unknown")
            ext = os.path.splitext(filename)[1].lower()
            if ext not in SUPPORTED_EXTENSIONS:
                skipped.append(f"{filename} (unsupported type)")
                continue
            url = f.get("url_private_download") or f.get("url_private")
            if not url:
                skipped.append(f"{filename} (no download link)")
                continue
            dest = os.path.join(tmp_dir, filename)
            try:
                download_slack_file(url, dest)
                saved.append((dest, filename))
            except Exception as e:
                skipped.append(f"{filename} (download error: {e})")

        if not saved:
            client.chat_postMessage(
                channel=channel,
                text="Couldn't read any of those files. " + " ".join(skipped),
            )
            return

        ranked, errors = run_screening(jd_text, saved, max_workers=MAX_WORKERS)

        if not ranked:
            client.chat_postMessage(
                channel=channel,
                text="No resumes could be scored. " + " ".join(errors + skipped),
            )
            return

        client.chat_postMessage(channel=channel, text=f":clipboard: *{len(ranked)} candidate(s) ranked*")

        for c in ranked:
            client.chat_postMessage(channel=channel, text=format_candidate(c))

        problems = errors + skipped
        if problems:
            client.chat_postMessage(channel=channel, text=":warning: Skipped: " + "; ".join(problems))

        client.files_upload_v2(
            channel=channel,
            filename="resume_screening_results.csv",
            content=build_results_csv(ranked),
            title="Ranked results (CSV)",
            initial_comment="Full ranked results as a spreadsheet:",
        )
    finally:
        # Downloaded resumes are only needed long enough to read + score.
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.event("message")
def handle_message(event, say, client, logger):
    if event.get("bot_id"):
        return

    user = event.get("user")
    channel = event.get("channel")
    files = event.get("files", [])
    text = (event.get("text") or "").strip()

    if files:
        jd_text = pending_jd.get(user)
        if not jd_text:
            say("I don't have a job description yet. Please paste the JD text first, then drop the resumes.")
            return

        say(f":hourglass_flowing_sand: Screening {len(files)} resume(s) against your JD… I'll post the ranked results here shortly.")

        threading.Thread(
            target=run_and_post,
            args=(client, channel, jd_text, files),
            daemon=True,
        ).start()

        pending_jd.pop(user, None)
        return

    if text:
        pending_jd[user] = text
        say(
            f"Got the job description ({len(text)} characters).\n"
            f"Now drop the resume files here — PDF, DOCX, TXT, or images."
        )
        return

    say("Send me a job description as text first, then drop the resume files.")


@app.event("app_mention")
def handle_mention(event, say):
    say("DM me directly: paste a job description, then drop the resumes.")


# --- Flask app: this is what Render/gunicorn serves ---
flask_app = Flask(__name__)
handler = SlackRequestHandler(app)


@flask_app.route("/slack/events", methods=["POST"])
def slack_events():
    # Bolt handles the URL-verification challenge and all events here.
    return handler.handle(request)


@flask_app.route("/", methods=["GET"])
def health():
    # Simple health check so visiting the URL (or Render's checker) gets a 200.
    return "Resume screener bot is running.", 200


if __name__ == "__main__":
    # Local development server. In production, gunicorn serves flask_app.
    port = int(os.environ.get("PORT", 3000))
    print(f"Starting Slack bot (HTTP mode) on port {port} ...")
    flask_app.run(host="0.0.0.0", port=port)