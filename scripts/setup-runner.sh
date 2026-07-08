#!/usr/bin/env bash
#
# setup-runner.sh — one-shot install of the self-hosted GitHub Actions runner
# that runs the `deploy` job (see .github/workflows/ci.yml).
#
# You can't mint the registration token yourself (the repo is owned by another
# user account and you're a WRITE collaborator — runner management needs the
# owner). Ask the owner for a token, then run:
#
#   ./scripts/setup-runner.sh <REGISTRATION_TOKEN>
#
# The token is short-lived (~1h) and only used to register; once configured the
# runner stays connected on its own. Owner mints it via:
#   UI:  repo > Settings > Actions > Runners > New self-hosted runner (Linux)
#   CLI: gh api --method POST repos/<owner>/<repo>/actions/runners/registration-token --jq .token
#
# Idempotent-ish: safe to re-run; if a runner is already configured in the target
# dir it tells you how to reconfigure rather than clobbering it.

set -euo pipefail

# --- config (override via env if needed) ------------------------------------
RUNNER_VERSION="${RUNNER_VERSION:-2.335.1}"    # actions/runner release, no leading v
RUNNER_LABEL="${RUNNER_LABEL:-flatchat-prod}"  # MUST match runs-on in ci.yml
RUNNER_NAME="${RUNNER_NAME:-flatchat-prod-host}"
RUNNER_DIR="${RUNNER_DIR:-$HOME/actions-runner}"

# --- args -------------------------------------------------------------------
TOKEN="${1:-${RUNNER_TOKEN:-}}"
if [ -z "$TOKEN" ]; then
  echo "usage: $0 <REGISTRATION_TOKEN>   (or set RUNNER_TOKEN)" >&2
  echo "get the token from the repo OWNER — see the header of this script." >&2
  exit 2
fi

# --- derive the repo URL from the git remote (no hardcoded owner/repo) -------
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REMOTE_URL="$(git -C "$REPO_ROOT" remote get-url origin)"
# git@github.com:owner/repo.git  OR  https://github.com/owner/repo(.git) -> https URL
REPO_SLUG="$(printf '%s' "$REMOTE_URL" \
  | sed -E 's#^git@github\.com:##; s#^https://github\.com/##; s#\.git$##')"
REPO_URL="https://github.com/${REPO_SLUG}"

# --- arch -> runner asset ---------------------------------------------------
case "$(uname -m)" in
  x86_64)          RUNNER_ARCH="x64" ;;
  aarch64|arm64)   RUNNER_ARCH="arm64" ;;
  *) echo "unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac
TARBALL="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
URL="https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"

log() { printf '\n\033[1;36m▶ %s\033[0m\n' "$*"; }

log "Repo:   $REPO_URL"
log "Runner: name=$RUNNER_NAME label=$RUNNER_LABEL dir=$RUNNER_DIR v$RUNNER_VERSION ($RUNNER_ARCH)"

# --- download + extract (skip if already present) ---------------------------
mkdir -p "$RUNNER_DIR"
cd "$RUNNER_DIR"
if [ -x "./config.sh" ]; then
  log "Runner binaries already present in $RUNNER_DIR — skipping download"
else
  log "Downloading $TARBALL"
  curl -fL -o "$TARBALL" "$URL"
  tar xzf "$TARBALL"
  rm -f "$TARBALL"
fi

# --- refuse to clobber an existing registration -----------------------------
if [ -f "./.runner" ]; then
  echo
  echo "A runner is already configured in $RUNNER_DIR." >&2
  echo "To re-register: (sudo ./svc.sh stop && sudo ./svc.sh uninstall);" >&2
  echo "  ./config.sh remove --token <REMOVE_TOKEN>   # remove token, also owner-minted" >&2
  echo "then re-run this script with a fresh registration token." >&2
  exit 1
fi

# --- configure --------------------------------------------------------------
log "Registering runner"
./config.sh \
  --url "$REPO_URL" \
  --token "$TOKEN" \
  --labels "$RUNNER_LABEL" \
  --name "$RUNNER_NAME" \
  --unattended \
  --replace

# --- install as a service (survives reboots) --------------------------------
log "Installing + starting the runner service"
sudo ./svc.sh install
sudo ./svc.sh start
sudo ./svc.sh status || true

# --- docker access sanity ---------------------------------------------------
if ! id -nG "$USER" | grep -qw docker; then
  log "Adding $USER to the docker group (needed for deploy.sh to drive compose)"
  sudo usermod -aG docker "$USER"
  echo "NOTE: group change needs a fresh login (or reboot) to take effect for" >&2
  echo "      interactive shells. The runner service picks it up on its next start:" >&2
  echo "      cd $RUNNER_DIR && sudo ./svc.sh stop && sudo ./svc.sh start" >&2
fi

log "Done. The runner should show as Idle under the repo's Settings > Actions > Runners."
echo "Next: merge to main — CI runs, then the deploy job lands here (label: $RUNNER_LABEL)."
