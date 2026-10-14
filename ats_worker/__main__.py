"""python -m ats_worker [run | once | check | review-file PDF | authorize]"""
import argparse
import json
import logging
import sys
from pathlib import Path

from . import config


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ats_worker", description="Resume ATS review worker.")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="poll the portal and review resumes (default)")
    sub.add_parser("once", help="review at most one waiting resume, then exit")
    sub.add_parser("check", help="check the model and the Drive credentials")
    rf = sub.add_parser("review-file", help="review a local PDF and print the result (no portal, no Drive)")
    rf.add_argument("pdf", type=Path)
    rf.add_argument("--role", default="", help="target role")
    au = sub.add_parser("authorize", help="sign in to Google once and save token.json for Drive")
    au.add_argument("--client-secrets", type=Path, default=Path("credentials.json"),
                    help="OAuth client (Desktop app) JSON from Google Cloud Console")
    args = parser.parse_args(argv)

    cfg = config.load()
    logging.basicConfig(level=cfg.log_level, format="%(asctime)s %(levelname)s %(message)s")

    if args.cmd in (None, "run", "once"):
        from .worker import run
        run(cfg, once=args.cmd == "once")
    elif args.cmd == "check":
        return check(cfg)
    elif args.cmd == "review-file":
        return review_file(cfg, args.pdf, args.role)
    elif args.cmd == "authorize":
        from .drive import authorize
        print(f"Saved {authorize(args.client_secrets, cfg.drive_credentials)}")
    return 0


def check(cfg):
    from .drive import Drive
    from .llm import LLM
    ok = True
    problem = LLM(cfg).health()
    print(f"model   {cfg.llm_model} via {cfg.llm_backend} at {cfg.llm_url}: {problem or 'ready'}")
    ok &= problem is None
    try:
        Drive(cfg.drive_credentials)
        print(f"drive   {cfg.drive_credentials}: loaded")
    except SystemExit as e:
        print(f"drive   {e}")
        ok = False
    print(f"portal  {cfg.portal_url}: token {'set' if cfg.worker_token else 'MISSING'} "
          "(checked on the first claim)")
    ok &= bool(cfg.worker_token)
    return 0 if ok else 1


def review_file(cfg, pdf, role):
    from .llm import LLM
    from .review import analyse
    out = analyse(pdf.read_bytes(), role, LLM(cfg), cfg)
    if out.action == "failed":
        print(f"FAILED: {out.reason}")
        return 1
    print(json.dumps(out.result, indent=2, ensure_ascii=False))
    print(f"\nrule-based ATS score: {out.rules_score}", file=sys.stderr)
    for reason, text in out.hidden[:20]:
        print(f"hidden ({reason}): {text[:80]}", file=sys.stderr)
    print(f"flags: {out.flags or 'none'}  -> the portal would {'HOLD' if out.flags else 'publish'} this review",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
