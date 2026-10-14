# ATS worker

Reviews resumes uploaded at **learn.parottasalna.com/resume-review/** with a local model.
It runs on any machine with Python 3.10+ and a model server. Nothing calls in to it: it polls the
portal, so it works behind NAT with no open ports.

```
portal  ──claim──►  worker ──download──► Google Drive
        ◄─result──    │
                      └─► local model (Ollama / vLLM / llama.cpp)
```

For each job it:

1. claims the next resume (`POST /api/ats/claim/`). It receives a tracking ID and a Drive file ID,
   never the student's name or email
2. downloads the PDF from Drive
3. extracts the text a person would see, **dropping hidden text** (white on white, tiny, invisible,
   transparent, off-page)
4. normalises Unicode and **removes lines that talk to the AI** ("ignore previous instructions…")
5. asks the model for JSON in a fixed schema (`ats_worker/prompt.py`), with the resume fenced in a random
   tag and marked as data, at temperature 0
6. compares the model's score with a rule-based ATS checklist, and sets the verdict from the quality score
7. posts the result (`/complete/`). If the portal rejects it (422), the errors go back to the model, up to
   3 tries. Anything suspicious is sent as a flag, and the portal holds that review until an admin checks it

Unreadable PDFs (scans without OCR, password-protected) and non-resumes are reported with `/fail/`. The
student then sees a fixed message, never text from this machine.

## Set up on a new machine

```bash
# 1. copy this folder (git clone, scp, or a USB stick), then:
cd /opt/ats-worker
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. a model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen2.5:14b          # pick one that fits your GPU, see below

# 3. settings
cp .env.example .env             # set ATS_WORKER_TOKEN (same as the portal) and LLM_MODEL

# 4. Google Drive access. Simplest: copy the token the portal made with  manage.py drive_authorize
#    here as drive-token.json (the default DRIVE_CREDENTIALS). Otherwise, sign in separately (read-only):
#    needs credentials.json, an OAuth client of type "Desktop app"
#    from Google Cloud Console, for the Google account that owns the resume folder
.venv/bin/python -m ats_worker authorize --client-secrets credentials.json
#    On a machine without a browser, run this on your laptop and copy token.json over.
#    Or use a service-account key that is a member of the shared drive: DRIVE_CREDENTIALS=sa.json

# 5. check, then run
.venv/bin/python -m ats_worker check
.venv/bin/python -m ats_worker run
```

To keep it running across reboots, use the systemd unit in `deploy/ats-worker.service` (instructions
are inside it).

## Run it in containers (any Linux with Podman or Docker; no Python needed on the host)

```bash
./run.sh setup     # first time: Ollama + model + worker image
./run.sh check     # model ready? Drive token loads? token set?
./run.sh start     # (re)create the worker from .env + drive-token.json
./run.sh logs      # follow the log
./run.sh status    # what's running
./run.sh update    # rebuild from this folder's code and restart
./run.sh boot      # start again after a reboot (rootless Podman)
```

## Commands

| | |
|---|---|
| `python -m ats_worker run` | poll and review forever (default) |
| `python -m ats_worker once` | review at most one waiting resume, then exit |
| `python -m ats_worker check` | is the model ready, do the Drive credentials load, is the token set |
| `python -m ats_worker review-file cv.pdf --role "Data Engineer"` | review a local PDF and print the JSON, the hidden text found and the flags. No portal or Drive needed. Use it to try models and prompt changes |
| `python -m unittest` | tests (no model needed) |

## Choosing a model

Larger models follow the schema and calibrate scores better. Measured here on CPU with `qwen2.5:3b`:
2 to 3 minutes per resume, usable output.

| GPU memory | Ollama model |
|---|---|
| 8 GB | `qwen2.5:7b`, `llama3.1:8b` |
| 12–16 GB | `qwen2.5:14b` (recommended) |
| 24 GB+ | `qwen2.5:32b`, `gemma3:27b` |

Change `LLM_MODEL` in `.env` and restart. For vLLM or llama.cpp's server, set `LLM_BACKEND=openai` and
`LLM_URL=http://host:port/v1`.

## When things go wrong

- **Model down:** the worker stops taking jobs until it's back (it checks before every claim).
- **Worker crashes or is stopped mid-review:** the portal gives the job out again after 30 minutes;
  after 3 attempts it is marked failed.
- **`portal refused the token`:** `ATS_WORKER_TOKEN` differs from the portal's.
- Logs contain tracking IDs only, never resume text.
# ats-worker
