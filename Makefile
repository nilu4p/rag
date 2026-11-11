# Assistant RAG local : Ollama sur le PC, tout le reste dans Docker.
#   make install   → première installation complète
#   make           → aide

SHELL := /bin/bash
DC := docker compose
RUN := $(DC) exec -T rag python -m rag.cli
ENVV = $(shell grep -E '^$(1)=' .env 2>/dev/null | tail -n1 | cut -d= -f2-)

.DEFAULT_GOAL := help
.PHONY: help env install ollama-config ollama-check models build up down restart ps logs open n8n status docs doctor \
        ask web index reindex n8n-setup n8n-import backup restore update clean purge stop-ancien test

help: ## Affiche cette aide
	@echo "Usage : make <cible>"
	@grep -hE '^[a-zA-Z_-]+:.*## ' Makefile | awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

env:
	@bash scripts/env.sh
	@mkdir -p documents/ajouts backups && chmod 700 backups

install: env ## Première installation : configure Ollama si besoin, télécharge modèles et images, démarre
	@./scripts/ollama-host.sh check || ./scripts/ollama-host.sh config
	@$(MAKE) --no-print-directory models build up

ollama-check: env ## Vérifie qu'Ollama (sur le PC) est installé, démarré et joignable par Docker
	@./scripts/ollama-host.sh check

ollama-config: env ## Configure Ollama du PC pour Docker + réglages mémoire (sudo, une seule fois)
	@./scripts/ollama-host.sh config

models: env ## Télécharge les modèles du .env dans Ollama (CHAT_MODEL, EMBED_MODEL)
	@./scripts/ollama-host.sh models

build: env ## Construit/télécharge les images Docker
	$(DC) pull --ignore-buildable --quiet
	$(DC) build

up: env ## Démarre tout et affiche les adresses
	@./scripts/ollama-host.sh check || echo "  (le chat ne pourra pas répondre tant qu'Ollama n'est pas joignable)"
	$(DC) up -d --build --remove-orphans
	@printf "→ Attente du service rag "; for i in $$(seq 90); do \
	  $(DC) exec -T rag python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=2)" >/dev/null 2>&1 && break; \
	  printf "."; sleep 2; done; echo
	@$(DC) exec -T rag python -m rag.n8n_setup || true
	@$(RUN) status || true
	@echo
	@echo "  Chat       : http://localhost:$(call ENVV,RAG_PORT)   (identifiant : $(call ENVV,CHAT_USER) / mot de passe : $(call ENVV,CHAT_PASSWORD))"
	@echo "  n8n        : $(call ENVV,N8N_PUBLIC_URL)   ($(call ENVV,N8N_OWNER_EMAIL) / $(call ENVV,N8N_OWNER_PASSWORD))"
	@echo "  Documents  : $(CURDIR)/documents  (indexés automatiquement en moins d'une minute)"

down: ## Arrête tout (données conservées)
	$(DC) down --remove-orphans

restart: down up ## Redémarre tout

ps: ## État des conteneurs
	$(DC) ps -a

logs: ## Logs en direct (un service : make logs s=rag)
	$(DC) logs -f --tail=100 $(s)

open: ## Ouvre le chat dans le navigateur
	@xdg-open "http://localhost:$(call ENVV,RAG_PORT)" >/dev/null 2>&1 &

n8n: ## Ouvre n8n dans le navigateur
	@xdg-open "$(call ENVV,N8N_PUBLIC_URL)" >/dev/null 2>&1 &

status: ## Modèles, documents indexés, indexation en cours
	@$(RUN) status

docs: ## Liste les documents et leur état d'indexation
	@$(RUN) docs

doctor: ## Diagnostic complet (Ollama, embeddings, LLM, Qdrant, internet, mémoire)
	@./scripts/ollama-host.sh check || true
	@$(RUN) doctor

ask: ## Question depuis le terminal : make ask Q="Quel est mon parcours ?"
	@$(RUN) ask "$(Q)"

web: ## Question posée directement à internet : make web Q="Météo à Cayenne demain ?"
	@$(RUN) ask "$(Q)" web

index: ## Indexe tout de suite les fichiers nouveaux/modifiés/supprimés
	@$(RUN) index

reindex: ## Vide l'index et relit tous les documents
	@$(RUN) reindex

n8n-setup: ## Recrée le compte n8n / réactive les workflows du projet
	@$(DC) exec -T rag python -m rag.n8n_setup

n8n-import: ## Réimporte les workflows du projet dans n8n (⚠ écrase tes modifications)
	$(DC) run --rm -e FORCE_IMPORT=1 n8n-import
	$(DC) restart n8n
	@$(DC) exec -T rag python -m rag.n8n_setup

backup: env ## Sauvegarde maintenant (conversations, état, n8n, .env) dans backups/
	@f=backups/manuel-$$(date +%Y%m%d-%H%M%S); \
	  $(DC) run --rm --no-deps -T --entrypoint sh backup -c "tar -czf /backups/$$(basename $$f).tar.gz -C /data rag n8n" \
	  && cp .env $$f.env && chmod 600 $$f.env \
	  && echo "✔ Sauvegarde : $$f.tar.gz (+ .env). L'index Qdrant se reconstruit depuis documents/."

restore: ## Restaure une sauvegarde : make restore B=backups/auto-AAAAMMJJ.tar.gz
	@test -f "$(B)" || { echo "Usage : make restore B=backups/<fichier>.tar.gz"; ls backups/*.tar.gz 2>/dev/null; exit 1; }
	@read -p "Remplacer les conversations et les données n8n actuelles par $(B) ? [o/N] " r && [ "$$r" = "o" ]
	$(DC) stop rag n8n
	docker run --rm -v mon-rag_rag_state:/data/rag -v mon-rag_n8n_data:/data/n8n -v "$(abspath $(B)):/r.tar.gz:ro" alpine:3.20 \
	  sh -c 'mkdir -p /tmp/r && tar -xzf /r.tar.gz -C /tmp/r && for d in rag n8n; do if [ -d /tmp/r/$$d ]; then rm -rf /data/$$d/* && cp -a /tmp/r/$$d/. /data/$$d/; fi; done && echo "✔ Restauré"'
	@$(MAKE) --no-print-directory up

update: ## Met à jour les images Docker et relance
	$(DC) pull --ignore-buildable
	$(DC) build --pull
	@$(MAKE) --no-print-directory up

clean: ## ⚠ Supprime conteneurs ET données (index, conversations, n8n). Les documents sont conservés.
	@read -p "Supprimer conteneurs, index, conversations et données n8n ? (documents/ conservé) [o/N] " r && [ "$$r" = "o" ]
	$(DC) down -v --remove-orphans

purge: ## ⚠ Efface TOUTES les données perso (index, conversations, n8n, journaux, sauvegardes, documents, .env) — GARDER_DOCS=1 garde documents/
	@GARDER_DOCS="$(GARDER_DOCS)" OUI="$(OUI)" bash scripts/purge.sh

stop-ancien: ## Arrête l'ancienne version (projet rag-local) pour libérer la RAM et le port 5678
	-docker compose -p rag-local --profile local-ollama --profile tools down --remove-orphans
	@echo "Ses données sont conservées (volumes rag-local_*). Pour les supprimer : docker volume rm \$$(docker volume ls -q | grep '^rag-local_')"

test: ## Tests automatiques du code Python (dans le conteneur)
	$(DC) run --rm --no-deps -T -v "$(CURDIR)/app/tests:/app/tests:ro" rag python -m pytest -q -p no:cacheprovider tests
