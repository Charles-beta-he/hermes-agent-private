#!/usr/bin/env bash
# Charles private Hermes bootstrap installer.
#
# This script is safe to run from outside a checkout. It recommends the GitHub
# CLI path, validates private-repo access, clones/updates the private checkout,
# then delegates to scripts/install-private.sh for channel stamping and setup.
# It never accepts or stores GitHub tokens.

set -euo pipefail

PRIVATE_REPO_SLUG="${HERMES_PRIVATE_REPO_SLUG:-Charles-beta-he/hermes-agent-private}"
PRIVATE_REPO_HTTPS="${HERMES_PRIVATE_REPO_HTTPS:-https://github.com/${PRIVATE_REPO_SLUG}.git}"
PRIVATE_REPO_SSH="${HERMES_PRIVATE_REPO_SSH:-git@github.com:${PRIVATE_REPO_SLUG}.git}"
PRIVATE_BRANCH="${HERMES_PRIVATE_BRANCH:-private/main}"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
INSTALL_DIR="${HERMES_INSTALL_DIR:-$HERMES_HOME/hermes-agent}"
SKIP_SETUP=false
INSTALL_EXPERIENCE_PACK=true
DRY_RUN=false
YES=false
PREFER_SSH=false

usage() {
  cat <<'EOF'
Charles private Hermes bootstrap installer

Recommended user flow:
  1. Charles grants your GitHub account access to the private repo.
  2. Run:
       gh auth login
       gh auth setup-git
       gh api -H "Accept: application/vnd.github.raw" \
         repos/Charles-beta-he/hermes-agent-private/contents/scripts/bootstrap-private.sh?ref=private/main \
         | bash

Usage:
  bootstrap-private.sh [--yes] [--dry-run] [--skip-setup] [--skip-experience-pack] [--dir PATH] [--ssh]

Options:
  --yes          Non-interactive where safe; backup official checkout if found.
  --dry-run      Print actions without cloning/updating/running setup.
  --skip-setup   Clone/stamp channel metadata but skip ./setup-hermes.sh.
  --skip-experience-pack
                  Do not copy sanitized capability templates into HERMES_HOME.
  --dir PATH     Install checkout directory (default: ~/.hermes/hermes-agent).
  --ssh          Prefer SSH clone even when gh HTTPS credentials are available.

No GitHub token is accepted on the command line. Use gh auth or SSH keys.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --yes|-y) YES=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    --skip-setup) SKIP_SETUP=true; shift ;;
    --skip-experience-pack) INSTALL_EXPERIENCE_PACK=false; shift ;;
    --ssh) PREFER_SSH=true; shift ;;
    --dir) INSTALL_DIR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

info() { printf '→ %s\n' "$*"; }
success() { printf '✓ %s\n' "$*"; }
warn() { printf '⚠ %s\n' "$*"; }
fail() { printf '✗ %s\n' "$*" >&2; exit 1; }

run() {
  if [[ "$DRY_RUN" == true ]]; then
    printf 'DRY-RUN:'
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
  else
    "$@"
  fi
}

confirm_or_fail() {
  local prompt="$1"
  if [[ "$YES" == true ]]; then
    return 0
  fi
  printf '%s [y/N] ' "$prompt"
  local answer=""
  read -r answer || answer=""
  case "${answer,,}" in
    y|yes) return 0 ;;
    *) fail "Aborted." ;;
  esac
}

print_recommendation() {
  cat <<EOF
Recommended private install path:
  gh auth login
  gh auth setup-git
  gh api -H "Accept: application/vnd.github.raw" \\
    repos/${PRIVATE_REPO_SLUG}/contents/scripts/bootstrap-private.sh?ref=${PRIVATE_BRANCH} \\
    | bash

This bootstrap will now automate the clone/update/finalize steps for:
  repo:   ${PRIVATE_REPO_SLUG}
  branch: ${PRIVATE_BRANCH}
  dir:    ${INSTALL_DIR}
EOF
}

check_install_dir_safety() {
  local dir="$1"
  [[ -n "$dir" ]] || fail "Install directory is empty."
  [[ "$dir" != "/" ]] || fail "Refusing to install into /."
  [[ "$dir" != "$HOME" ]] || fail "Refusing to install directly into HOME."
  [[ "$dir" != "$HERMES_HOME" ]] || fail "Refusing to use HERMES_HOME itself as the source checkout; use $HERMES_HOME/hermes-agent."
}

ensure_access() {
  if [[ "$DRY_RUN" == true ]]; then
    info "Dry-run: would verify GitHub CLI or SSH access to $PRIVATE_REPO_SLUG"
    return 0
  fi

  if command -v gh >/dev/null 2>&1; then
    if gh auth status >/dev/null 2>&1; then
      info "GitHub CLI authenticated; checking private repo access"
      if gh repo view "$PRIVATE_REPO_SLUG" >/dev/null 2>&1; then
        success "GitHub access confirmed for $PRIVATE_REPO_SLUG"
        return 0
      fi
      fail "GitHub CLI is authenticated, but cannot access $PRIVATE_REPO_SLUG. Accept the invite or ask Charles to grant this GitHub account access."
    fi
    warn "GitHub CLI is installed but not authenticated."
    echo "  Run: gh auth login && gh auth setup-git"
  else
    warn "GitHub CLI not found. Install gh or use --ssh with a configured GitHub SSH key."
  fi

  if GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=5" git ls-remote "$PRIVATE_REPO_SSH" "$PRIVATE_BRANCH" >/dev/null 2>&1; then
    success "SSH access confirmed for $PRIVATE_REPO_SLUG"
    PREFER_SSH=true
    return 0
  fi

  fail "Cannot access $PRIVATE_REPO_SLUG. Recommended fix: gh auth login && gh auth setup-git, or configure SSH and run with --ssh."
}

clone_or_update_checkout() {
  local dir="$1"
  local clone_url="$PRIVATE_REPO_HTTPS"
  if [[ "$PREFER_SSH" == true ]]; then
    clone_url="$PRIVATE_REPO_SSH"
  fi

  if [[ ! -e "$dir" ]]; then
    info "Cloning private Hermes checkout"
    run mkdir -p "$(dirname "$dir")"
    run git clone --branch "$PRIVATE_BRANCH" --single-branch "$clone_url" "$dir"
    return 0
  fi

  if [[ ! -d "$dir/.git" ]]; then
    fail "$dir exists but is not a git checkout. Move it aside or pass --dir PATH."
  fi

  local origin=""
  origin="$(git -C "$dir" remote get-url origin 2>/dev/null || true)"
  case "$origin" in
    *"$PRIVATE_REPO_SLUG"*)
      info "Existing private checkout found; refreshing $PRIVATE_BRANCH"
      run git -C "$dir" fetch origin "$PRIVATE_BRANCH"
      run git -C "$dir" checkout -B "$PRIVATE_BRANCH" "origin/$PRIVATE_BRANCH"
      ;;
    *NousResearch/hermes-agent*)
      warn "Official Hermes checkout found at $dir. User data under $HERMES_HOME will be preserved; only the code checkout moves aside."
      confirm_or_fail "Back up the official checkout and install Charles private Hermes here?"
      local backup="${dir}.official-backup.$(date +%Y%m%d-%H%M%S)"
      info "Moving official checkout to $backup"
      run mv "$dir" "$backup"
      run git clone --branch "$PRIVATE_BRANCH" --single-branch "$clone_url" "$dir"
      ;;
    "")
      fail "$dir has no origin remote; refusing to guess. Move it aside or pass --dir PATH."
      ;;
    *)
      warn "Unexpected origin at $dir: $origin"
      confirm_or_fail "Back up this checkout and replace it with Charles private Hermes?"
      local backup="${dir}.backup.$(date +%Y%m%d-%H%M%S)"
      run mv "$dir" "$backup"
      run git clone --branch "$PRIVATE_BRANCH" --single-branch "$clone_url" "$dir"
      ;;
  esac
}

finalize_install() {
  local args=("$INSTALL_DIR/scripts/install-private.sh" --yes)
  if [[ "$SKIP_SETUP" == true ]]; then
    args+=(--skip-setup)
  fi
  if [[ "$INSTALL_EXPERIENCE_PACK" != true ]]; then
    args+=(--skip-experience-pack)
  fi
  if [[ "$DRY_RUN" == true ]]; then
    args+=(--dry-run)
  fi
  info "Finalizing private Hermes channel"
  run bash "${args[@]}"
}

print_recommendation
check_install_dir_safety "$INSTALL_DIR"
ensure_access
clone_or_update_checkout "$INSTALL_DIR"
finalize_install

success "Private Hermes installation flow complete"
printf '  checkout: %s\n' "$INSTALL_DIR"
printf '  branch:   %s\n' "$PRIVATE_BRANCH"
printf '  update:   hermes update\n'
