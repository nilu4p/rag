#!/usr/bin/env bash
# Ollama installé sur le PC (hôte) : vérification, configuration, modèles.
#   check  : Ollama installé, démarré, joignable depuis Docker ?
#   config : le fait écouter pour Docker + réglages mémoire (sudo, une seule fois)
#   models : télécharge les modèles du .env s'ils manquent
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; X=$'\033[0m'
CHAT_MODEL=${CHAT_MODEL:-gpt-oss:20b}; EMBED_MODEL=${EMBED_MODEL:-bge-m3}

listen_addr() { ss -ltnH 2>/dev/null | awk '{print $4}' | grep -E ':11434$' | head -n1; }

install_hint() {
  echo "${R}✘ Ollama n'est pas installé sur ce PC.${X} Installe-le avec :"
  echo "    curl -fsSL https://ollama.com/install.sh | sh"
  exit 1
}

check() {
  command -v ollama >/dev/null 2>&1 || install_hint
  if ! ollama list >/dev/null 2>&1; then
    echo "${R}✘ Ollama est installé mais ne répond pas.${X} Démarre-le : sudo systemctl start ollama"
    exit 1
  fi
  local addr; addr=$(listen_addr)
  case "$addr" in
    127.0.0.1:*|"[::1]:"*|localhost:*)
      echo "${Y}⚠ Ollama n'écoute que sur $addr : les conteneurs Docker ne peuvent pas le joindre.${X}"
      echo "  → lance une fois : ${G}make ollama-config${X}"
      exit 1 ;;
    *) echo "${G}✔${X} Ollama du PC joignable par Docker (écoute sur ${addr:-?})" ;;
  esac
}

config() {
  command -v ollama >/dev/null 2>&1 || install_hint
  if ! systemctl cat ollama >/dev/null 2>&1; then
    echo "${Y}Ollama n'est pas géré par systemd ici.${X} Démarre-le avec ces variables d'environnement :"
    echo "  OLLAMA_HOST=0.0.0.0:11434 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=2 ollama serve"
    exit 1
  fi
  echo "→ Configuration d'Ollama (mot de passe sudo demandé) :"
  echo "   - écoute sur toutes les interfaces, pour que Docker puisse le joindre"
  echo "   - 1 requête à la fois + cache compressé + flash attention : ~2 fois moins de RAM"
  sudo mkdir -p /etc/systemd/system/ollama.service.d
  sudo tee /etc/systemd/system/ollama.service.d/mon-rag.conf >/dev/null <<'CONF'
# Créé par mon_RAG (make ollama-config). Supprime ce fichier pour revenir aux réglages d'origine.
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_FLASH_ATTENTION=1"
Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Environment="OLLAMA_KEEP_ALIVE=30m"
CONF
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  sleep 2
  # Pare-feu : Docker autorisé, reste du réseau local bloqué
  if command -v ufw >/dev/null 2>&1 && sudo ufw status 2>/dev/null | grep -q "Status: active"; then
    sudo ufw allow from 172.16.0.0/12 to any port 11434 proto tcp >/dev/null
    echo "${G}✔${X} Pare-feu : port 11434 ouvert aux réseaux Docker"
  else
    echo "${Y}ℹ${X} Ollama (port 11434) est maintenant joignable depuis ton réseau local."
    echo "  Sur un réseau non fiable, active le pare-feu : sudo ufw enable && sudo ufw allow from 172.16.0.0/12 to any port 11434"
  fi
  check
}

models() {
  command -v ollama >/dev/null 2>&1 || install_hint
  for m in "$EMBED_MODEL" "$CHAT_MODEL"; do
    if ollama show "$m" >/dev/null 2>&1; then
      echo "${G}✔${X} Modèle $m présent"
    else
      echo "⬇ Téléchargement de $m (une seule fois)…"
      ollama pull "$m"
    fi
  done
}

"${1:-check}"
