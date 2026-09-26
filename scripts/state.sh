#!/usr/bin/env bash
# Sauvegarde / restauration chiffrée de la base CRM sur la branche "crm-data".
# Le dépôt est public : la base n'y est JAMAIS stockée en clair.
# Usage : YWIAD_STATE_KEY=... scripts/state.sh pull|push
set -euo pipefail
: "${YWIAD_STATE_KEY:?YWIAD_STATE_KEY manquante (variable Netlify du site yourwebsiteinaday)}"
DB="${YWIAD_DB:-data/ywiad.sqlite}"
BRANCH=crm-data
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

case "${1:-}" in
  pull)
    if git fetch -q origin "$BRANCH" 2>/dev/null; then
      git show "origin/$BRANCH:ywiad.sqlite.enc" > "$TMP/db.enc"
      mkdir -p "$(dirname "$DB")"
      openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass env:YWIAD_STATE_KEY -in "$TMP/db.enc" -out "$DB"
      echo "Base restaurée : $DB"
    else
      echo "Pas encore de branche $BRANCH : nouvelle base."
    fi
    ;;
  push)
    [ -f "$DB" ] || { echo "Base absente : $DB" >&2; exit 1; }
    sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
    openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass env:YWIAD_STATE_KEY -in "$DB" -out "$TMP/ywiad.sqlite.enc"
    git -C "$TMP" init -q
    git -C "$TMP" checkout -q -b "$BRANCH"
    ( cd "$TMP" && git add ywiad.sqlite.enc && \
      git -c user.name="ywiad-bot" -c user.email="ywiad-bot@users.noreply.github.com" commit -qm "CRM chiffré — $(date -u +%FT%TZ)" )
    git -C "$TMP" push -qf "$(git remote get-url origin)" "$BRANCH:$BRANCH"
    echo "Base chiffrée poussée sur $BRANCH"
    ;;
  *)
    echo "Usage : $0 pull|push" >&2; exit 2 ;;
esac
