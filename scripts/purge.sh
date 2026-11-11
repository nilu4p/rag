#!/usr/bin/env bash
# Efface TOUTES les données personnelles du projet, pour repartir de zéro ou publier le code (GitHub…).
#   make purge                  → liste ce qui va être effacé et demande de taper EFFACER
#   make purge GARDER_DOCS=1    → conserve le dossier documents/
#   make purge OUI=1            → sans confirmation (scripts)
#
# Effacé : base de connaissance (index Qdrant), conversations et état d'indexation (SQLite),
#          données n8n, conteneurs et leurs journaux, sauvegardes, documents, .env (secrets),
#          caches Python, volumes de l'ancienne version (rag-local).
# Conservé : le code, les images Docker, les modèles Ollama et la configuration systemd d'Ollama.
set -euo pipefail
cd "$(dirname "$0")/.."
G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; X=$'\033[0m'
GARDER_DOCS=${GARDER_DOCS:-0}
PROJECTS=(mon-rag rag-local)   # projet actuel + ancienne version

nfiles() { [ -d "$1" ] && find "$1" -type f ! -name .gitkeep | wc -l || echo 0; }
have_docker() { command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; }

# Vide un dossier, y compris les fichiers créés par root depuis un conteneur
wipe() {
  local dir=$1
  [ -d "$dir" ] || return 0
  if ! find "$dir" -mindepth 1 -delete 2>/dev/null; then
    have_docker || { echo "${R}✘ Impossible de vider $dir (droits) : sudo rm -rf $dir/*${X}"; return 1; }
    docker run --rm -v "$PWD/$dir:/w" alpine:3.20 find /w -mindepth 1 -delete
  fi
}

# ---------------------------------------------------------------- récapitulatif
volumes=""
if have_docker; then
  for p in "${PROJECTS[@]}"; do
    volumes+=" $(docker volume ls -q --filter "label=com.docker.compose.project=$p" | tr '\n' ' ')"
  done
fi
echo "${R}⚠  Effacement DÉFINITIF des données personnelles du RAG :${X}"
echo "   - base de connaissance, conversations, état d'indexation, données n8n (volumes :${volumes:- aucun})"
echo "   - conteneurs du projet et leurs journaux"
echo "   - sauvegardes : backups/ ($(nfiles backups) fichier(s))"
if [ "$GARDER_DOCS" = 1 ]; then
  echo "   ${G}(documents/ conservé : GARDER_DOCS=1)${X}"
else
  echo "   - tes documents : documents/ ($(nfiles documents) fichier(s))   ${Y}← make purge GARDER_DOCS=1 pour les garder${X}"
fi
echo "   - .env (mots de passe et secrets, régénérés au prochain make install), caches Python"
echo "   Conservés : le code, les images Docker, les modèles Ollama, la configuration systemd d'Ollama."
if [ "${OUI:-0}" != 1 ]; then
  read -r -p "Tape EFFACER pour confirmer : " r
  [ "$r" = "EFFACER" ] || { echo "Annulé, rien n'a été effacé."; exit 1; }
fi

# ---------------------------------------------------------------- Docker : conteneurs (+ journaux) et volumes
if have_docker; then
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
  for p in "${PROJECTS[@]}"; do
    ids=$(docker ps -aq --filter "label=com.docker.compose.project=$p")
    [ -z "$ids" ] || docker rm -f $ids >/dev/null || echo "${R}✘ Conteneurs non supprimés ($p)${X}"
    vols=$(docker volume ls -q --filter "label=com.docker.compose.project=$p")
    [ -z "$vols" ] || docker volume rm $vols >/dev/null || echo "${R}✘ Volumes non supprimés : $vols${X}"
  done
  echo "${G}✔${X} Conteneurs, journaux et volumes supprimés (index, conversations, n8n)"
else
  echo "${Y}⚠ Docker indisponible : volumes non supprimés (relance make purge une fois Docker démarré)${X}"
fi

# ---------------------------------------------------------------- fichiers
wipe backups && mkdir -p backups && chmod 700 backups
echo "${G}✔${X} Sauvegardes supprimées"
if [ "$GARDER_DOCS" != 1 ]; then
  wipe documents
  echo "${G}✔${X} Documents supprimés"
fi
mkdir -p documents/ajouts && touch documents/ajouts/.gitkeep
rm -f .env
find . -maxdepth 1 -name '.env.*' ! -name .env.example -delete
find app -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf .pytest_cache app/.pytest_cache
echo "${G}✔${X} .env et caches supprimés"

# ---------------------------------------------------------------- vérification
left=$(( $(nfiles backups) + ( GARDER_DOCS == 1 ? 0 : $(nfiles documents) ) ))
[ -f .env ] && left=$((left + 1))
if have_docker; then
  for p in "${PROJECTS[@]}"; do
    left=$((left + $(docker volume ls -q --filter "label=com.docker.compose.project=$p" | wc -l)))
  done
fi
if [ "$left" -eq 0 ]; then
  echo "${G}✔ Plus aucune donnée personnelle dans le projet.${X} Pour repartir : make install"
else
  echo "${R}✘ $left élément(s) n'ont pas pu être effacés (voir les messages ci-dessus).${X}"
  exit 1
fi
