"""Review one PDF: extract the visible text, apply the input guardrails, ask the model, check the answer.

With `submit` (the portal's complete call) a rejected answer is sent back to the model with the
validator's errors, up to MODEL_TRIES times. Without it (review-file), the answer is just returned.
"""
import logging
from collections import Counter
from dataclasses import dataclass, field

from . import guards, pdftext
from .llm import LLMError
from .prompt import SCHEMA, WORKER_ONLY, build_messages, retry_message, verdict_for

log = logging.getLogger(__name__)

MODEL_TRIES = 3
MIN_TEXT_CHARS = 100
FLAG_LEN = 100       # the portal keeps up to 10 flags of 100 characters


@dataclass
class Outcome:
    action: str                 # "completed", "failed" (with reason) or "lost" (the portal took the job back)
    reason: str = ""
    result: dict = None
    status: str = ""            # the portal's answer: "done" or "held"
    flags: list = field(default_factory=list)
    rules_score: int = None
    hidden: list = field(default_factory=list)


def _short(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def _hidden_flag(hidden):
    kinds = Counter(reason for reason, _ in hidden)
    sample = _short(" ".join(text for _, text in hidden), 30)
    return _short(f"hidden text removed ({', '.join(f'{k}: {n}' for k, n in kinds.most_common(2))}): “{sample}”",
                  FLAG_LEN)


def analyse(pdf, target_role, llm, cfg, submit=None):
    ext = pdftext.extract(pdf, max_pages=cfg.max_pages, ocr=cfg.ocr)
    text = guards.normalise(ext.text)
    if ext.encrypted or len(text) < MIN_TEXT_CHARS:
        return Outcome("failed", reason="unreadable")

    flags = []
    if ext.hidden:
        flags.append(_hidden_flag(ext.hidden))
    phrases = guards.find_injection(text)
    if phrases:
        flags.append(_short(f"text addressed to the AI removed: “{phrases[0]}”", FLAG_LEN))
        text = guards.redact_injection(text)
    if ext.used_ocr:
        log.info("no text layer; used OCR")
    text = text[:cfg.max_chars]
    rules = guards.rule_based_score(text)

    messages = build_messages(text, target_role)
    for attempt in range(1, MODEL_TRIES + 1):
        raw = _ask(llm, messages)
        extra = []
        if raw.get("is_resume") is False:
            if rules < 40:
                return Outcome("failed", reason="not_a_resume", rules_score=rules)
            extra.append(f"model said 'not a resume' but rule-based score is {rules}")
        if raw.get("contains_instructions"):
            extra.append("model noticed text aimed at the AI")
        extra += guards.check_scores(raw, rules)
        result = {k: v for k, v in raw.items() if k not in WORKER_ONLY}
        if isinstance(result.get("quality_score"), int) and not isinstance(result["quality_score"], bool):
            result["verdict"] = verdict_for(max(0, min(100, result["quality_score"])))
        result["flags"] = [_short(f, FLAG_LEN) for f in flags + extra][:10]
        if submit is None:
            return Outcome("completed", result=result, flags=result["flags"], rules_score=rules, hidden=ext.hidden)

        code, body = submit(result)
        if code == 200:
            return Outcome("completed", result=result, status=body.get("status", ""), flags=body.get("flags", []),
                           rules_score=rules)
        if code == 409:
            return Outcome("lost")
        if code == 422:
            errors = body.get("errors") or ["invalid result"]
            log.warning("portal rejected the answer (try %d/%d): %s", attempt, MODEL_TRIES, "; ".join(errors)[:300])
            messages = messages[:2] + [{"role": "assistant", "content": _json(raw)}, retry_message(errors)]
            continue
        raise LLMError(f"unexpected portal answer HTTP {code}")
    return Outcome("failed", reason="error")


def _ask(llm, messages):
    last = None
    for _ in range(MODEL_TRIES):
        try:
            return llm.chat_json(messages, SCHEMA)
        except LLMError as e:
            last = e
            log.warning("%s; retrying", e)
    raise last


def _json(data):
    import json
    return json.dumps(data, ensure_ascii=False)
