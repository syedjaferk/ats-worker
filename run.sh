#!/bin/sh
# Run the ATS worker (and its Ollama model server) in containers. Works with Podman or Docker.
#
#   ./run.sh setup     first time: start Ollama, pull the model, build the worker image
#   ./run.sh start     (re)create the worker container from .env + drive-token.json
#   ./run.sh stop      stop and remove the worker container
#   ./run.sh logs      follow the worker's log
#   ./run.sh status    what's running, and the model list
#   ./run.sh check     is the model ready, do the Drive credentials load, is the token set
#   ./run.sh update    rebuild the image from this folder's code, then start again
#   ./run.sh boot      start the containers automatically after a reboot (rootless Podman)
set -eu
cd "$(dirname "$0")"

ENGINE=$(command -v podman >/dev/null 2>&1 && echo podman || echo docker)
OLLAMA_IMAGE=docker.io/ollama/ollama:0.15.1
MODEL=$(sed -n 's/^LLM_MODEL=//p' .env 2>/dev/null | tail -1)
MODEL=${MODEL:-qwen2.5:3b}
# :z relabels the files for SELinux (Rocky, Fedora, RHEL) so containers may read them; Docker on Ubuntu
# ignores it. Lowercase on purpose: :Z would label them for ONE container, and the next one (./run.sh check)
# would take them away from the running worker.
MOUNTS="-v $PWD/.env:/app/.env:ro,z -v $PWD/drive-token.json:/app/drive-token.json:ro,z"

need_files() {
    for f in .env drive-token.json; do
        [ -f "$f" ] || { echo "Missing $PWD/$f (see .env.example and README.md)." >&2; exit 1; }
    done
}

case "${1:-}" in
    setup)
        if ! $ENGINE container exists ollama 2>/dev/null && ! $ENGINE ps -a --format '{{.Names}}' | grep -qx ollama; then
            $ENGINE run -d --name ollama --network host --restart unless-stopped \
                -v ollama:/root/.ollama "$OLLAMA_IMAGE"
            sleep 5
        fi
        $ENGINE start ollama >/dev/null
        $ENGINE exec ollama ollama pull "$MODEL"
        $ENGINE build -t ats-worker .
        echo "Setup done. Next: ./run.sh check, then ./run.sh start"
        ;;
    start)
        need_files
        # After a reboot (without ./run.sh boot) Ollama sits stopped; the worker would just wait for it.
        $ENGINE start ollama >/dev/null 2>&1 || echo "Ollama isn't set up yet: run ./run.sh setup" >&2
        # Recreate (not restart): .env and the token are mounted as files, and an edited file is a
        # new file, which a running container would never see.
        $ENGINE rm -f ats-worker >/dev/null 2>&1 || true
        # shellcheck disable=SC2086
        $ENGINE run -d --name ats-worker --network host --restart unless-stopped $MOUNTS localhost/ats-worker
        echo "Started. Follow it with: ./run.sh logs"
        ;;
    stop)
        $ENGINE rm -f ats-worker
        ;;
    logs)
        $ENGINE logs -f --tail 50 ats-worker
        ;;
    status)
        $ENGINE ps -a --format '{{.Names}}\t{{.Status}}' | grep -E '^(ats-worker|ollama)\b' || echo "Nothing running."
        $ENGINE exec ollama ollama list 2>/dev/null || echo "Ollama isn't running (./run.sh setup)."
        ;;
    check)
        need_files
        # shellcheck disable=SC2086
        $ENGINE run --rm --network host $MOUNTS localhost/ats-worker python -m ats_worker check
        ;;
    update)
        $ENGINE build -t ats-worker .
        "$0" start
        ;;
    boot)
        if [ "$ENGINE" != podman ]; then
            echo "Docker restarts --restart containers at boot by itself." && exit 0
        fi
        systemctl --user enable --now podman-restart.service
        loginctl enable-linger "$(id -un)" || echo "Run: sudo loginctl enable-linger $(id -un)"
        ;;
    *)
        sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
        exit 1
        ;;
esac
