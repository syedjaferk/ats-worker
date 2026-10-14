"""The polling loop: wait for the model to be ready, claim a job, review it, report back, repeat.

Nothing about the student (name, email) ever reaches this machine: only the Drive file and its tracking ID.
Logs carry tracking IDs, never resume text.
"""
import logging
import signal
import threading
import time

from .drive import Drive, DriveError
from .llm import LLM, LLMError
from .portal import Portal, PortalError
from .review import analyse

log = logging.getLogger(__name__)


def process_job(job, portal, drive, llm, cfg):
    tid = job["tracking_id"]
    started = time.monotonic()
    log.info("%s: claimed (attempt %s)", tid, job.get("attempt"))
    try:
        pdf = drive.download(job["drive_file_id"])
    except DriveError as e:
        if e.missing:
            log.warning("%s: %s; marking failed", tid, e)
            portal.fail(tid, "error")
            return
        raise
    outcome = analyse(pdf, job.get("target_role") or "", llm, cfg,
                      submit=lambda result: portal.complete(tid, result))
    took = time.monotonic() - started
    if outcome.action == "failed":
        portal.fail(tid, outcome.reason)
        log.info("%s: failed (%s) in %.0fs", tid, outcome.reason, took)
    elif outcome.action == "lost":
        log.warning("%s: the portal no longer assigns this job to us (took too long?)", tid)
    else:
        log.info("%s: %s, ATS %s, rules %s, in %.0fs%s", tid, outcome.status, outcome.result["ats_score"],
                 outcome.rules_score, took, f"; flags: {outcome.flags}" if outcome.flags else "")


def run(cfg, once=False):
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: (log.info("stopping after the current job"), stop.set()))

    portal, drive, llm = Portal(cfg.portal_url, cfg.worker_token), Drive(cfg.drive_credentials), LLM(cfg)
    log.info("worker started: %s, model %s (%s)", cfg.portal_url, cfg.llm_model, cfg.llm_backend)
    idle_logged = False
    while not stop.is_set():
        problem = llm.health()
        if problem:
            log.warning("model not ready (%s); not taking jobs", problem)
            stop.wait(max(cfg.poll_seconds, 60))
            continue
        try:
            job = portal.claim()
        except PortalError as e:
            log.warning("%s", e)
            stop.wait(max(cfg.poll_seconds, 60))
            continue
        if job is None:
            if once:
                log.info("no jobs waiting")
                return
            if not idle_logged:
                log.info("queue empty; polling every %ss", cfg.poll_seconds)
                idle_logged = True
            stop.wait(cfg.poll_seconds)
            continue
        idle_logged = False
        try:
            process_job(job, portal, drive, llm, cfg)
        except (LLMError, DriveError, PortalError) as e:
            # Temporary trouble: leave the job; the portal hands it out again after its claim timeout.
            log.error("%s: %s; leaving it for a retry", job["tracking_id"], e)
            stop.wait(cfg.poll_seconds)
        except Exception:
            log.exception("%s: unexpected error; marking failed", job["tracking_id"])
            try:
                portal.fail(job["tracking_id"], "error")
            except PortalError:
                pass
        if once:
            return
