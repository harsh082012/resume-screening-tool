"""
cli.py
Command-line batch runner: reads every resume from a folder, compares each
against a JD file, scores + ranks them, and writes a CSV report.

This satisfies the "extract resumes from a folder" requirement directly,
independent of the web UI, and is handy for large batches / automation.

Usage:
    python cli.py --jd sample_jd.txt --resumes_dir sample_resumes --output outputs/results.csv
"""

import argparse
import csv
import os
import sys

from dotenv import load_dotenv

# IMPORTANT: load .env BEFORE importing any `core` modules. core.llm_client
# reads LLM_PROVIDER etc. from the environment at import time (module-level
# code), so if load_dotenv() ran after that import, it would already be too
# late and llm_client would silently fall back to its "gemini" default.
load_dotenv()

from core.extractor import extract_text, load_resumes_from_folder
from core.scorer import score_resume, rank_candidates
from core.llm_client import LLMError


def main():
    parser = argparse.ArgumentParser(description="Batch resume screening against a JD.")
    parser.add_argument("--jd", required=True, help="Path to the job description file (.txt/.pdf/.docx)")
    parser.add_argument("--resumes_dir", required=True, help="Folder containing resume files")
    parser.add_argument("--output", default="outputs/results.csv", help="Path to write the ranked CSV report")
    args = parser.parse_args()

    if not os.path.isfile(args.jd):
        print(f"JD file not found: {args.jd}")
        sys.exit(1)

    jd_text = extract_text(args.jd)
    if not jd_text:
        print("Could not extract any text from the JD file.")
        sys.exit(1)

    print(f"Loaded JD ({len(jd_text)} characters) from {args.jd}")
    print(f"Scanning resumes in: {args.resumes_dir}")

    resumes = load_resumes_from_folder(args.resumes_dir)
    if not resumes:
        print("No readable resumes found in that folder.")
        sys.exit(1)

    print(f"Found {len(resumes)} resume(s). Scoring against the JD...\n")

    scored = []
    for r in resumes:
        print(f"  Scoring: {r['filename']} ...", end=" ", flush=True)
        try:
            result = score_resume(r["text"], jd_text, filename=r["filename"])
            scored.append(result)
            print(f"ATS score: {result['ats_score']}")
        except LLMError as e:
            print(f"FAILED ({e})")

    if not scored:
        print("\nNo resumes were successfully scored.")
        sys.exit(1)

    ranked = rank_candidates(scored)

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "Rank", "Candidate Name", "Source File", "ATS Score",
            "Skills Match", "Experience Match", "Education Match",
            "Years Experience (est.)", "Matched Skills", "Missing Skills", "Summary",
        ])
        for c in ranked:
            writer.writerow([
                c["rank"], c["candidate_name"], c["source_filename"], c["ats_score"],
                c["skills_match_score"], c["experience_match_score"], c["education_match_score"],
                c["years_experience_estimate"],
                "; ".join(c["matched_skills"]), "; ".join(c["missing_skills"]), c["summary"],
            ])

    print(f"\nRanked results written to: {args.output}\n")
    print(f"{'Rank':<5}{'Candidate':<28}{'Score':<7}File")
    print("-" * 70)
    for c in ranked:
        print(f"{c['rank']:<5}{c['candidate_name'][:26]:<28}{c['ats_score']:<7}{c['source_filename']}")


if __name__ == "__main__":
    main()
