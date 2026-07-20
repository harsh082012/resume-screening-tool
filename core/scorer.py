"""
scorer.py
Builds the evaluation prompt sent to the LLM for a single resume vs a single
JD, and returns a structured score + breakdown + improvement suggestions.

Scoring is RUBRIC-BASED for consistency: the sub-scores are tied to countable
criteria, and the headline ats_score is a FIXED weighted formula of those
sub-scores rather than a holistic gut judgment. This makes the same resume +
JD produce the same score run-to-run (instead of drifting 70 -> 61 -> 62),
because the model is doing defined arithmetic, not re-forming an opinion.
"""

from core.llm_client import call_llm_json, LLMError


PROMPT_TEMPLATE = """You are an expert technical recruiter and ATS (Applicant Tracking System) evaluator.
Score the candidate's RESUME against the JOB DESCRIPTION using the STRICT RUBRIC below.
Follow the rubric mechanically. Do NOT use holistic intuition — compute each number from the rules.

JOB DESCRIPTION:
\"\"\"
{jd_text}
\"\"\"

CANDIDATE RESUME:
\"\"\"
{resume_text}
\"\"\"

=== STEP 1: SKILLS ===
- Identify the list of REQUIRED skills/technologies/tools named in the JOB DESCRIPTION. Call this count N.
- Count how many of those N the resume CLEARLY demonstrates (M). A skill counts only if the resume
  explicitly shows it — do not infer generously.
- skills_match_score = round(100 * M / N).  If N is 0, skills_match_score = 50.
- Put the demonstrated ones in matched_skills and the rest in missing_skills.

=== STEP 2: EXPERIENCE ===
- Determine the minimum years of relevant experience the JD asks for (R). Estimate the candidate's
  relevant years (C) from the resume.
- If the JD specifies R:
    - If C >= R:  experience_match_score = 100
    - If C <  R:  experience_match_score = round(100 * C / R)
- If the JD does NOT specify years: experience_match_score = 100 if clearly relevant experience is
  present, else 50.

=== STEP 3: EDUCATION ===
- If the JD states an education requirement:
    - Meets or exceeds it:            education_match_score = 100
    - Related/partial (adjacent field or one level below): education_match_score = 60
    - Does not meet it:               education_match_score = 20
- If the JD states NO education requirement: education_match_score = 100.

=== STEP 4: OVERALL SCORE (FIXED FORMULA — DO NOT DEVIATE) ===
ats_score = round( 0.50 * skills_match_score
                 + 0.30 * experience_match_score
                 + 0.20 * education_match_score )
Compute this exactly from the three numbers above. Do not add or subtract points based on gut feel.

Respond with ONLY a valid JSON object (no markdown fences, no preamble, no text outside the JSON)
in exactly this shape:

{{
  "candidate_name": "<best guess at the candidate's name from the resume, or 'Unknown' if not found>",
  "ats_score": <integer 0-100, computed with the Step 4 formula>,
  "skills_match_score": <integer 0-100 from Step 1>,
  "experience_match_score": <integer 0-100 from Step 2>,
  "education_match_score": <integer 0-100 from Step 3>,
  "matched_skills": ["<required skill from JD the resume clearly demonstrates>", ...],
  "missing_skills": ["<required skill/requirement from JD missing or unclear in the resume>", ...],
  "years_experience_estimate": "<short string, e.g. '4-5 years' or 'Not specified'>",
  "summary": "<2-3 sentence honest recruiter-style summary of this candidate's fit for this specific role>",
  "improvement_suggestions": [
    "<specific, actionable change the CANDIDATE could make to better match this JD — name a missing keyword/tool, quantify an achievement, or surface buried relevant experience>",
    "..."
  ]
}}

Rules:
- Be strict and realistic, not generous. Count a skill as matched only when the resume clearly shows it.
- matched_skills and missing_skills must reference concrete terms/technologies/requirements from the JD.
- improvement_suggestions must be specific and based on an actual gap between THIS resume and THIS JD,
  never generic advice like "add more detail". Provide 3-5 suggestions.
- The three sub-scores and the ats_score MUST be internally consistent with the formula in Step 4.
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

    # Enforce the Step 4 formula ourselves so the headline score is fully
    # deterministic given the sub-scores, even if the model's arithmetic drifts.
    try:
        skills = float(result.get("skills_match_score", 0) or 0)
        exp = float(result.get("experience_match_score", 0) or 0)
        edu = float(result.get("education_match_score", 0) or 0)
        result["ats_score"] = round(0.50 * skills + 0.30 * exp + 0.20 * edu)
    except (TypeError, ValueError):
        pass  # keep whatever the model returned if sub-scores are unparseable

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