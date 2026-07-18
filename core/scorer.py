"""
scorer.py
Builds the evaluation prompt sent to the LLM for a single resume vs a single
JD, and returns a structured score + breakdown + improvement suggestions.
"""

from core.llm_client import call_llm_json, LLMError


PROMPT_TEMPLATE = """You are an expert technical recruiter and ATS (Applicant Tracking System) evaluator.
Compare the candidate's RESUME against the JOB DESCRIPTION below and evaluate fit objectively.

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

CANDIDATE RESUME:
\"\"\"
{resume_text}
\"\"\"

Evaluate the resume against the job description and respond with ONLY a valid JSON object
(no markdown fences, no preamble, no explanation outside the JSON) in exactly this shape:

{{
  "candidate_name": "<best guess at the candidate's name from the resume, or 'Unknown' if not found>",
  "ats_score": <integer 0-100, overall weighted fit score>,
  "skills_match_score": <integer 0-100>,
  "experience_match_score": <integer 0-100>,
  "education_match_score": <integer 0-100>,
  "matched_skills": ["<skill from JD that the resume clearly demonstrates>", ...],
  "missing_skills": ["<important skill/requirement from JD that is missing or unclear in the resume>", ...],
  "years_experience_estimate": "<short string, e.g. '4-5 years' or 'Not specified'>",
  "summary": "<2-3 sentence honest recruiter-style summary of this candidate's fit for this specific role>",
  "improvement_suggestions": [
    "<specific, actionable suggestion for what the CANDIDATE could add or rephrase in their resume to better match this JD, e.g. naming a missing keyword/tool, quantifying an achievement, or surfacing relevant experience that may be buried>",
    "..."
  ]
}}

Scoring guidance:
- ats_score should weigh skills match most heavily, then experience relevance, then education.
- Be strict and realistic, not generous. A resume that only loosely relates to the JD should score low.
- matched_skills and missing_skills should reference concrete terms/technologies/requirements from the JD.
- improvement_suggestions must be specific and actionable, not generic advice like "add more detail". Base every suggestion on an actual gap between this resume and this JD.
- Provide 3-5 improvement_suggestions.
"""


def score_resume(resume_text: str, jd_text: str, filename: str = "") -> dict:
    """
    Score a single resume against a JD using the LLM.
    Returns a dict with the parsed evaluation, plus the source filename attached.
    Raises LLMError if the model call or JSON parsing fails.
    """
    prompt = PROMPT_TEMPLATE.format(jd_text=jd_text.strip(), resume_text=resume_text.strip())

    result = call_llm_json(prompt)

    # Defensive defaults in case the model omits a field
    result.setdefault("candidate_name", filename or "Unknown")
    result.setdefault("ats_score", 0)
    result.setdefault("skills_match_score", 0)
    result.setdefault("experience_match_score", 0)
    result.setdefault("education_match_score", 0)
    result.setdefault("matched_skills", [])
    result.setdefault("missing_skills", [])
    result.setdefault("years_experience_estimate", "Not specified")
    result.setdefault("summary", "")
    result.setdefault("improvement_suggestions", [])

    result["source_filename"] = filename
    return result


def rank_candidates(scored_candidates: list[dict]) -> list[dict]:
    """
    Sort a list of scored candidate dicts by ats_score descending and
    attach a 1-indexed rank field to each.
    """
    ranked = sorted(scored_candidates, key=lambda c: c.get("ats_score", 0), reverse=True)
    for i, candidate in enumerate(ranked, start=1):
        candidate["rank"] = i
    return ranked
