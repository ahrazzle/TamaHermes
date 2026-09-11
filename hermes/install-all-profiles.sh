#!/usr/bin/env sh
# Install (or refresh) TamaHermes as the pet in every Hermes profile on this machine.
#
#   ./hermes/install-all-profiles.sh
#   ./hermes/install-all-profiles.sh --only halakukhan,lugia
#   ./hermes/install-all-profiles.sh --keep-selection
#
# Hermes pets are profile-scoped, so each profile needs its own install:
# <HERMES_HOME>/pets/tamahermes plus <HERMES_HOME>/plugins/tamahermes.
# The default profile lives at ~/.hermes; named profiles at
# ~/.hermes/profiles/<name>.
#
# Options:
#   --only a,b,c        Only these profiles (default: all found)
#   --keep-selection    Install the pet but leave display.pet.slug alone
#   --petdex            Also float the pet on the desktop via Petdex.app
#   --petdex-activate   ...and make it the active desktop pet (close Petdex first)
#   --line / --machine  Passed through to the per-profile installer
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
HERMES_BASE="${HERMES_BASE:-$HOME/.hermes}"

ONLY=""
KEEP_SELECTION=""
PASSTHRU=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --only) [ "$#" -ge 2 ] || { echo "--only requires a value" >&2; exit 2; }; ONLY="$2"; shift 2 ;;
    --only=*) ONLY=${1#*=}; shift ;;
    --keep-selection) KEEP_SELECTION="1"; shift ;;
    --line|--machine|--form|--display-name|--petdex-home)
      [ "$#" -ge 2 ] || { echo "$1 requires a value" >&2; exit 2; }
      PASSTHRU="$PASSTHRU $1 $2"; shift 2 ;;
    # Machine-level flag, but it is passed per profile on purpose: each profile
    # home gets its own pointer so that profile's plugin mirrors rebuilds too.
    --petdex|--petdex-activate)
      PASSTHRU="$PASSTHRU $1"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

selected() {
  # No --only means everything.
  [ -z "$ONLY" ] && return 0
  case ",$ONLY," in
    *",$1,"*) return 0 ;;
    *) return 1 ;;
  esac
}

# A profile is any dir in the base (or profiles/) that has a config.yaml, plus
# the base home itself (the default profile).
collect_homes() {
  if [ -f "$HERMES_BASE/config.yaml" ]; then
    printf '%s\n' "$HERMES_BASE"
  fi
  for dir in "$HERMES_BASE"/profiles/*/; do
    [ -d "$dir" ] || continue
    [ -f "${dir}config.yaml" ] || continue
    printf '%s\n' "${dir%/}"
  done
}

profile_name() {
  case "$1" in
    "$HERMES_BASE") printf 'default' ;;
    */profiles/*) basename "$1" ;;
    *) basename "$1" ;;
  esac
}

OK_LIST=""
FAIL_LIST=""
SKIP_LIST=""

for home in $(collect_homes); do
  name=$(profile_name "$home")
  if ! selected "$name"; then
    SKIP_LIST="$SKIP_LIST $name"
    continue
  fi
  printf '\n=== %s  (%s) ===\n' "$name" "$home" >&2
  if HERMES_HOME="$home" "$SCRIPT_DIR/install-hermes.sh" $PASSTHRU >/dev/null 2>&1; then
    : # pet installed
  else
    echo "!! pet install failed for $name" >&2
    FAIL_LIST="$FAIL_LIST $name"
    continue
  fi
  if [ -n "$KEEP_SELECTION" ]; then
    :
  elif HERMES_HOME="$home" hermes pets select tamahermes >/dev/null 2>&1; then
    :
  else
    echo "!! could not select the pet for $name (install it manually)" >&2
  fi
  if HERMES_HOME="$home" hermes plugins enable tamahermes >/dev/null 2>&1; then
    OK_LIST="$OK_LIST $name"
  else
    echo "!! plugin enable failed for $name" >&2
    FAIL_LIST="$FAIL_LIST $name"
  fi
done

printf '\n===== summary =====\n'
printf 'installed+enabled:%s\n' "${OK_LIST:- (none)}"
[ -n "$SKIP_LIST" ] && printf 'skipped:          %s\n' "$SKIP_LIST"
[ -n "$FAIL_LIST" ] && printf 'failed:           %s\n' "$FAIL_LIST"
printf '\nVerify per profile:\n'
printf '  HERMES_HOME=<home> hermes pets doctor\n'
[ -n "$FAIL_LIST" ] && exit 1
exit 0
