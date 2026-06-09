#!/usr/bin/env bash
# Charles private Hermes installer finalizer.
#
# Preferred user flow:
#   gh auth login
#   gh auth setup-git
#   gh repo clone Charles-beta-he/hermes-agent-private ~/.hermes/hermes-agent -- --branch private/main --single-branch
#   ~/.hermes/hermes-agent/scripts/install-private.sh
#
# This script intentionally runs from an already-authorized private checkout.
# It does not accept or store GitHub tokens.

set -euo pipefail

PRIVATE_REPO_HTTPS="${HERMES_PRIVATE_REPO_HTTPS:-https://github.com/Charles-beta-he/hermes-agent-private.git}"
PRIVATE_REPO_SSH="${HERMES_PRIVATE_REPO_SSH:-git@github.com:Charles-beta-he/hermes-agent-private.git}"
OFFICIAL_UPSTREAM="${HERMES_OFFICIAL_UPSTREAM:-https://github.com/NousResearch/hermes-agent.git}"
PRIVATE_BRANCH="${HERMES_PRIVATE_BRANCH:-private/main}"
PRIVATE_CHANNEL="${HERMES_PRIVATE_CHANNEL:-charles-private}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
SKIP_SETUP=false
DRY_RUN=false
YES=false

usage() {
  cat <<'EOF'
Charles private Hermes installer finalizer

Usage:
  scripts/install-private.sh [--skip-setup] [--dry-run] [--yes]

Environment overrides:
  HERMES_HOME                 Data directory (default: ~/.hermes)
  HERMES_PRIVATE_BRANCH       Private update branch (default: private/main)
  HERMES_PRIVATE_REPO_HTTPS   Private origin HTTPS URL
  HERMES_PRIVATE_REPO_SSH     Private origin SSH URL
  HERMES_OFFICIAL_UPSTREAM    Official upstream URL

This script must be run inside the authorized private repository checkout.
It configures origin/upstream, stamps install metadata, then delegates to
./setup-hermes.sh unless --skip-setup is passed.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-setup) SKIP_SETUP=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --yes|-y) YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

info() { printf '→ %s\n' "$*"; }
success() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"

run() {
  if [[ "$DRY_RUN" == true ]]; then
    printf 'DRY-RUN: %q' "$1"
    shift || true
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
  else
    "$@"
  fi
}

cd "$repo_root"

if [[ ! -d .git ]]; then
  fail "$repo_root is not a git checkout. Clone the private repo first."
fi

current_origin="$(git remote get-url origin 2>/dev/null || true)"
case "$current_origin" in
  *Charles-beta-he/hermes-agent-private*|"" ) ;;
  *NousResearch/hermes-agent* )
    fail "This checkout still points at the official NousResearch repo. Clone Charles-beta-he/hermes-agent-private instead."
    ;;
  * )
    warn "origin is not the expected private repo: $current_origin"
    if [[ "$YES" != true ]]; then
      printf 'Continue and reset origin to the Charles private repo? [y/N] '
      read -r answer || answer=""
      case "${answer,,}" in y|yes) ;; *) fail "Aborted without changing remotes." ;; esac
    fi
    ;;
esac

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$branch" != "$PRIVATE_BRANCH" ]]; then
  info "Switching checkout to $PRIVATE_BRANCH"
  if [[ "$DRY_RUN" == true ]]; then
    echo "DRY-RUN: git fetch origin $PRIVATE_BRANCH"
    echo "DRY-RUN: git checkout -B $PRIVATE_BRANCH origin/$PRIVATE_BRANCH"
  else
    git fetch origin "$PRIVATE_BRANCH"
    git checkout -B "$PRIVATE_BRANCH" "origin/$PRIVATE_BRANCH"
  fi
fi

# Prefer preserving an existing SSH origin if the user cloned over SSH.
if [[ "$current_origin" == git@github.com:* ]]; then
  desired_origin="$PRIVATE_REPO_SSH"
else
  desired_origin="$PRIVATE_REPO_HTTPS"
fi

info "Configuring remotes"
if git remote get-url origin >/dev/null 2>&1; then
  run git remote set-url origin "$desired_origin"
else
  run git remote add origin "$desired_origin"
fi
if git remote get-url upstream >/dev/null 2>&1; then
  run git remote set-url upstream "$OFFICIAL_UPSTREAM"
else
  run git remote add upstream "$OFFICIAL_UPSTREAM"
fi
# Make upstream fetch-only for normal users. Git accepts a non-URL pushurl and
# fails closed if someone accidentally runs `git push upstream ...`.
run git remote set-url --push upstream DISABLED

info "Stamping private install metadata"
if [[ "$DRY_RUN" == true ]]; then
  cat <<EOF
DRY-RUN: write $HERMES_HOME/install.json
{
  "channel": "$PRIVATE_CHANNEL",
  "origin": "$desired_origin",
  "upstream": "$OFFICIAL_UPSTREAM",
  "branch": "$PRIVATE_BRANCH",
  "installer": "scripts/install-private.sh",
  "installer_version": 1
}
EOF
else
  mkdir -p "$HERMES_HOME"
  python3 - "$HERMES_HOME/install.json" "$PRIVATE_CHANNEL" "$desired_origin" "$OFFICIAL_UPSTREAM" "$PRIVATE_BRANCH" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "channel": sys.argv[2],
    "origin": sys.argv[3],
    "upstream": sys.argv[4],
    "branch": sys.argv[5],
    "installer": "scripts/install-private.sh",
    "installer_version": 1,
    "installed_at": datetime.now(timezone.utc).isoformat(),
}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
PY
  printf 'git\n' > "$HERMES_HOME/.install_method"
fi

if [[ "$SKIP_SETUP" == true ]]; then
  warn "Skipped ./setup-hermes.sh (--skip-setup)."
else
  info "Running setup-hermes.sh"
  run bash ./setup-hermes.sh
fi

success "Charles private Hermes channel is configured"
printf '  channel:  %s\n' "$PRIVATE_CHANNEL"
printf '  branch:   %s\n' "$PRIVATE_BRANCH"
printf '  origin:   %s\n' "$desired_origin"
printf '  upstream: %s\n' "$OFFICIAL_UPSTREAM"
printf '\n以后更新只需运行: hermes update\n'
