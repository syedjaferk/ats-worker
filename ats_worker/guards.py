"""Guardrails around the model: clean what goes in, sanity-check what comes out.

Going in:  normalise Unicode (homoglyphs, zero-width and bidi characters), find text that talks to the
           model ("ignore previous instructions…") and replace it, so it can't steer the review.
Coming out: compare the model's ATS score with a simple rule-based score of the same text. A model that
           was talked into "score this 100" will disagree wildly with the rules.

Everything found becomes a flag. The portal holds flagged reviews until a person has looked at them.
"""
import re
import unicodedata

# Zero-width, bidi-control and other invisible characters used to hide or scramble words.
INVISIBLE_RE = re.compile(r"[­͏؜ᅟᅠ឴឵᠎​-‏‪-‮"
                          r"⁠-⁤⁪-⁯ㅤ﻿ﾠ]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

INJECTION_PATTERNS = [
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|preceding|other) (?:instructions?|prompts?|rules|text)",
    r"disregard (?:all |any |the )?(?:previous|prior|above|earlier)?\s*(?:instructions?|prompts?|rules)",
    r"forget (?:all |any |the |your )?(?:previous |prior )?(?:instructions?|rules|prompts?)",
    r"(?:new|updated|real|actual) instructions?\s*:",
    r"system prompt|system message|developer mode|jailbreak",
    r"you are (?:now )?(?:an? )?(?:ai|assistant|chatbot|language model|llm|gpt|recruiter bot)",
    r"(?:note|message|instruction)s? (?:to|for) (?:the )?(?:ai|llm|model|assistant|chatgpt|gpt|ats|bot|reviewer)",
    r"(?:as an|dear) (?:ai|llm|language model|chatgpt)",
    r"(?:give|assign|rate|score|grade|mark)(?: this| the| my| me)?(?: resume| candidate| cv| profile)?"
    r" (?:a |an )?(?:score of |rating of )?(?:100|10\s*/\s*10|perfect|full marks|maximum)",
    r"(?:this|the) candidate (?:is|must be) (?:the )?(?:best|perfect|ideal|top)",
    r"\b(?:shortlist|hire|recommend) (?:this|the) candidate (?:immediately|without)",
    # "only" is required: "REST APIs that return JSON" is a normal resume line.
    r"(?:respond|reply|answer) only with|output only|return only (?:the |valid )?json",
    r"<\s*/?\s*(?:system|assistant|user|instructions?|resume[-\w]*)\s*>",
    r"\[/?(?:inst|system)\]|<\|(?:im_start|im_end|system|user|assistant)\|>",
]
INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in INJECTION_PATTERNS), re.IGNORECASE)

SCORE_DISAGREEMENT = 35   # points between the model's ATS score and the rule-based one before we flag it


def normalise(text):
    text = unicodedata.normalize("NFKC", text)   # fullwidth / stylised letters -> plain letters
    text = INVISIBLE_RE.sub("", text)
    text = CONTROL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def find_injection(text):
    """The distinct instruction-like phrases in the text (lower-cased), in order."""
    seen = []
    for m in INJECTION_RE.finditer(text):
        phrase = " ".join(m.group(0).lower().split())
        if phrase not in seen:
            seen.append(phrase)
    return seen


def redact_injection(text):
    """Replace each line that contains an instruction-like phrase."""
    return "\n".join("[removed: text addressed to the AI reviewer]" if INJECTION_RE.search(line) else line
                     for line in text.splitlines())


# ---------------------------------------------------------------- rule-based second opinion

SECTIONS = {
    "education": r"education|academic|qualification",
    "experience": r"experience|employment|work history|internships?",
    "projects": r"projects?",
    "skills": r"skills|technologies|tech stack|technical proficiency",
    "summary": r"summary|objective|profile|about me",
    "certifications": r"certifications?|courses|licenses",
}


def rule_based_score(text):
    """A plain ATS checklist score (0-100) from the visible text. Crude, but impossible to talk into a 100."""
    lower = text.lower()
    words = len(text.split())
    checks = {
        "email": (10, bool(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text))),
        "phone": (8, bool(re.search(r"(?:\+?\d[\d\s().-]{8,}\d)", text))),
        "linkedin or github": (7, "linkedin" in lower or "github" in lower),
        "length": (10, 250 <= words <= 1200),
        "bullets": (5, bool(re.search(r"^\s*[•\-*▪●◦·]", text, re.MULTILINE))),
        "numbers": (10, len(re.findall(r"\d+\s*%|\d+\+|\b\d{2,}\b", text)) >= 3),
        "action verbs": (10, len(re.findall(
            r"\b(?:built|developed|designed|implemented|led|created|improved|reduced|increased|automated|"
            r"deployed|optimi[sz]ed|managed|launched|migrated|integrated|analy[sz]ed)\b", lower)) >= 3),
    }
    score = sum(points for points, ok in checks.values() if ok)
    for name, pattern in SECTIONS.items():
        if re.search(rf"^\s*(?:{pattern})\b", lower, re.MULTILINE):
            score += 40 // len(SECTIONS) + (1 if name in ("education", "experience", "projects", "skills") else 0)
    return min(score, 100)


def check_scores(result, rules_score):
    flags = []
    ats = result.get("ats_score")
    if isinstance(ats, (int, float)) and abs(ats - rules_score) > SCORE_DISAGREEMENT:
        flags.append(f"model ATS score {ats} far from rule-based {rules_score}")
    return flags
