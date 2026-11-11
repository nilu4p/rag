#!/bin/sh
# Sauvegarde quotidienne : conversations et état de l'index (rag) + données n8n → backups/auto-AAAAMMJJ.tar.gz
# Les documents eux-mêmes restent dans documents/ (sauvegarde-les avec tes outils habituels).
KEEP=${BACKUP_KEEP_DAYS:-7}
umask 077
echo "Sauvegardes automatiques : 1 par jour, conservées ${KEEP} jours"
while true; do
  f="/backups/auto-$(date +%Y%m%d).tar.gz"
  if [ ! -s "$f" ]; then
    if tar -czf "$f.tmp" -C /data rag n8n 2>/dev/null && [ -s "$f.tmp" ]; then
      mv "$f.tmp" "$f" && echo "✔ $(basename "$f")"
    else
      rm -f "$f.tmp"; echo "✘ échec de la sauvegarde"
    fi
    find /backups -name 'auto-*.tar.gz' -mtime +"$KEEP" -delete
  fi
  sleep 3600
done
