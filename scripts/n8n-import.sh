#!/bin/sh
# Lancé avant chaque démarrage de n8n :
#  - identifiant « Accès RAG » (mêmes identifiants que le chat) recréé depuis le .env à chaque fois
#  - workflows du projet importés une fois par version (ensuite, tes modifications dans n8n sont conservées)
set -e
MARK=/home/node/.n8n/.rag-workflows-version
VERSION=1
CREDS=/tmp/rag-credentials

mkdir -p "$CREDS"
node -e '
const fs = require("fs"), e = process.env;
fs.writeFileSync(process.argv[1] + "/ragBasicAuth0001.json", JSON.stringify({
  id: "ragBasicAuth0001", name: "Accès RAG (identifiants du chat)", type: "httpBasicAuth",
  data: { user: e.CHAT_USER || "moi", password: e.CHAT_PASSWORD || "" },
}));
' "$CREDS"
n8n import:credentials --separate --input="$CREDS" >/dev/null
rm -rf "$CREDS"
echo "✔ Identifiants n8n à jour"

if [ "$(cat "$MARK" 2>/dev/null || true)" = "$VERSION" ] && [ "${FORCE_IMPORT:-0}" != 1 ]; then
  echo "✔ Workflows déjà importés (v$VERSION ; make n8n-import pour les réimporter)"
  exit 0
fi
for f in /import/workflows/*.json; do
  n8n import:workflow --input="$f"
done
echo "$VERSION" > "$MARK"
echo "✔ Workflows importés (activés ensuite par make up)"
