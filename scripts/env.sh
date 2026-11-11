#!/usr/bin/env bash
# Crée .env depuis .env.example, ou y ajoute les clés manquantes (sans toucher aux existantes).
#   __SECRET__   → secret aléatoire        __PASSWORD__ → mot de passe (majuscule + chiffre, accepté par n8n)
#   __UID__ / __GID__ → identifiants de l'utilisateur courant (droits sur documents/)
set -euo pipefail
cd "$(dirname "$0")/.."

rand() { openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n'; }
value() {
  case "$1" in
    __SECRET__)   rand ;;
    __PASSWORD__) echo "Rag-$(rand | cut -c1-10)A1" ;;
    __UID__)      id -u ;;
    __GID__)      id -g ;;
    *)            echo "$1" ;;
  esac
}

if [ ! -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^([A-Z0-9_]+)=(.*)$ ]]; then
      echo "${BASH_REMATCH[1]}=$(value "${BASH_REMATCH[2]}")"
    else
      echo "$line"
    fi
  done < .env.example > .env
  chmod 600 .env
  echo "✔ .env créé (mots de passe et secrets générés)"
  exit 0
fi

added=()
while IFS= read -r line || [ -n "$line" ]; do
  [[ "$line" =~ ^([A-Z0-9_]+)=(.*)$ ]] || continue
  key=${BASH_REMATCH[1]}
  grep -qE "^${key}=" .env && continue
  [ ${#added[@]} -eq 0 ] && printf '\n# --- Ajouté automatiquement (%s) ---\n' "$(date +%F)" >> .env
  echo "${key}=$(value "${BASH_REMATCH[2]}")" >> .env
  added+=("$key")
done < .env.example
[ ${#added[@]} -gt 0 ] && echo "✔ .env complété : ${added[*]}"
exit 0
