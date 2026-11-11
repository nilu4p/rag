# 10 — API HTTP

> [⬅ Précédent : 09 — Recherche internet](09-recherche-web.md) · [🏠 Sommaire](README.md) · [Suivant : 11 — Interface web ➡](11-interface-web.md)

**Sur cette page**
1. [Généralités](#1-généralités)
2. [Authentification](#2-authentification)
3. [Récapitulatif des routes](#3-récapitulatif-des-routes)
4. [Santé et état](#4-santé-et-état)
5. [Documents et indexation](#5-documents-et-indexation)
6. [Conversations](#6-conversations)
7. [Chat en streaming](#7-chat-en-streaming)
8. [Question simple (JSON)](#8-question-simple-json)
9. [Exemples d'intégration](#9-exemples-dintégration)

Fichier concerné : `app/rag/main.py`.

---

## 1. Généralités

| Élément | Valeur |
|---|---|
| Base URL (hôte) | `http://localhost:8000` (`RAG_PORT`) |
| Base URL (réseau Docker) | `http://rag:8000` |
| Format | JSON (UTF-8) ; streaming en **NDJSON** (`application/x-ndjson`) |
| Documentation OpenAPI | **désactivée** (`docs_url`, `redoc_url`, `openapi_url` = `None`) |
| En-têtes de sécurité | `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, CSP sur `/` et `/static` |

---

## 2. Authentification

Toutes les routes sauf `/api/health` (et `/static/*`) exigent **l'une** des deux méthodes :

| Méthode | En-tête | Usage typique |
|---|---|---|
| Jeton | `Authorization: Bearer <RAG_API_TOKEN>` | scripts, n8n, CLI |
| Basic | `Authorization: Basic base64(CHAT_USER:CHAT_PASSWORD)` | navigateur (fenêtre de connexion native) |

Comparaisons en temps constant (`secrets.compare_digest`). Échec → `401` avec `WWW-Authenticate: Basic realm="Assistant RAG"`. Si `CHAT_PASSWORD` est vide, l'accès Basic est refusé.

---

## 3. Récapitulatif des routes

| Méthode | Route | Rôle | Réponse |
|---|---|---|---|
| GET | `/` | Interface de chat | HTML |
| GET | `/static/*` | CSS, JS | fichiers |
| GET | `/api/health` | Vivacité (**sans auth**) | `{"ok": true}` |
| GET | `/api/status` | État des services | JSON |
| GET | `/api/doctor` | Diagnostic complet | JSON |
| GET | `/api/documents` | Liste des fichiers + indexation | JSON |
| GET | `/api/documents/file?path=` | Télécharger / afficher un original | fichier |
| POST | `/api/documents` | Déposer des fichiers (multipart) | JSON |
| DELETE | `/api/documents?path=` | Supprimer un fichier | JSON |
| POST | `/api/index` | Synchroniser / réindexer | JSON |
| GET | `/api/conversations` | Liste des conversations | JSON |
| GET | `/api/conversations/{cid}` | Une conversation + messages | JSON |
| PATCH | `/api/conversations/{cid}` | Renommer | JSON |
| DELETE | `/api/conversations/{cid}` | Supprimer | JSON |
| POST | `/api/chat` | Poser une question (streaming, enregistrée) | NDJSON |
| GET | `/api/chat/{mid}/stream` | Reprendre un flux | NDJSON |
| POST | `/api/chat/{mid}/stop` | Arrêter une réponse | JSON |
| POST | `/api/ask` | Question simple, réponse complète (non enregistrée) | JSON |

---

## 4. Santé et état

### `GET /api/health`
```json
{"ok": true}
```
Utilisé par le `HEALTHCHECK` Docker, `make up` et `cli.wait_ready()`.

### `GET /api/status`
```json
{
  "chat_model": "gpt-oss:20b",
  "embed_model": "bge-m3",
  "ollama_url": "http://host.docker.internal:11434",
  "web_search": true,
  "indexing": { "running": false, "done": 12, "total": 12, "current": "", "added": 12, "updated": 0,
                "removed": 0, "errors": [], "message": "12 ajouté(s), 0 mis à jour, 0 retiré(s)",
                "started_at": 1762790000.1, "finished_at": 1762790190.4 },
  "ollama": true,
  "models_missing": [],
  "loaded": ["bge-m3:latest"],
  "chunks": 312,
  "qdrant": true,
  "documents": 12,
  "documents_errors": 1
}
```
En cas de panne : `"ollama": false, "ollama_error": "…"` / `"qdrant": false, "qdrant_error": "…"`.

### `GET /api/doctor`
Exécute dans l'ordre (les étapes LLM sont sautées si les modèles manquent) :

| Test | Succès si |
|---|---|
| Ollama (modèles) | `CHAT_MODEL` et `EMBED_MODEL` installés |
| Embeddings | un embedding de « test » est produit (renvoie la dimension) |
| Qdrant | comptage des points |
| Recherche internet (SearXNG) | recherche « météo Paris » (ou « désactivée ») |
| LLM (génération) | « Réponds juste : OK » (400 tokens max) |
| Mémoire | > 4 Go disponibles (`/proc/meminfo`) |

```json
{"checks": [{"name": "Embeddings", "ok": true, "detail": "dimension 1024", "seconds": 0.4}, …], "indexing": {…}}
```

---

## 5. Documents et indexation

### `GET /api/documents`
```json
{
  "files": [
    {"path": "ajouts/cv.pdf", "sha": "…", "size": 120394, "mtime": 1762790000.0, "status": "ok",
     "error": null, "method": "texte", "pages": 2, "chunks": 6, "chars": 5120, "indexed_at": 1762790012.3}
  ],
  "indexing": { … },
  "supported": [".bmp", ".cfg", ".csv", …]
}
```
`status` ∈ `ok` · `empty` · `error`.

### `GET /api/documents/file?path=ajouts/cv.pdf`
Renvoie l'original, `Content-Disposition: inline`. 🔒 Le chemin est résolu et doit rester **sous** `documents/` (sinon `404`). Une CSP `sandbox` empêche un HTML déposé d'exécuter du script dans l'origine de l'application. Ajoute `#page=N` côté navigateur pour ouvrir un PDF à la bonne page.

### `POST /api/documents` (multipart, champ `files`, multiple)
```bash
curl -u moi:$PASS -F files=@facture.pdf -F files=@photo.jpg http://localhost:8000/api/documents
```
```json
{"saved": ["ajouts/facture.pdf", "ajouts/photo.jpg"], "refused": ["virus.exe (format non pris en charge)"]}
```
- Destination : `documents/ajouts/` ; nom nettoyé (150 car. max) ; suffixe ` (2)`, ` (3)`… si le nom existe.
- Écriture dans `.nom.part` puis renommage atomique.
- Taille > `MAX_FILE_MB` → refusé (« plus de 200 Mo »).
- Déclenche l'indexation immédiatement.

### `DELETE /api/documents?path=…`
Supprime le fichier **du disque** puis déclenche la synchro (qui le retire de l'index). → `{"deleted": "…"}`

### `POST /api/index`
| Corps | Comportement | Réponse |
|---|---|---|
| `{}` | réveille la surveillance (asynchrone) | `{"started": true, "indexing": {…}}` |
| `{"full": true}` | réindexation complète en tâche de fond | `{"started": true}` |
| `{"wait": true}` | attend la fin d'une indexation en cours, synchronise, **bloque** jusqu'à la fin | `{"done": true, "indexing": {…}, "message": "…"}` |
| `{"full": true, "wait": true}` | réindexation complète bloquante | idem |

---

## 6. Conversations

| Requête | Réponse |
|---|---|
| `GET /api/conversations` | `{"conversations": [{"id", "title", "created_at", "updated_at"}, …]}` (200 max, plus récentes d'abord) |
| `GET /api/conversations/{cid}` | `{"conversation": {…}, "messages": [{"id", "conversation_id", "role", "content", "meta", "status", "created_at", "live"}]}` — `live: true` si la réponse est encore en cours de génération |
| `PATCH /api/conversations/{cid}` `{"title": "…"}` (1–120 car.) | `{"ok": true}` |
| `DELETE /api/conversations/{cid}` | `{"ok": true}` (messages supprimés aussi) |

---

## 7. Chat en streaming

### `POST /api/chat`
```json
{"message": "Quand se termine mon contrat ?", "conversation_id": null, "mode": "auto"}
```
| Champ | Contraintes |
|---|---|
| `message` | 1 à 20 000 caractères |
| `conversation_id` | inconnu ou `null` → nouvelle conversation (titre = 60 premiers caractères) |
| `mode` | `auto` · `docs` · `web` · `chat` |

Réponse : flux NDJSON (une ligne JSON par événement), `Cache-Control: no-cache`, `X-Accel-Buffering: no` :
```
{"type": "meta", "conversation_id": "3f2a…", "message_id": "9b1c…"}
{"type": "status", "text": "Je comprends ta demande…"}
{"type": "status", "text": "Je cherche dans tes documents…"}
{"type": "status", "text": "Je rédige à partir de 4 extrait(s)…"}
{"type": "thinking"}
{"type": "token", "text": "Ton contrat se termine le **31 "}
{"type": "token", "text": "août 2027** [1]."}
{"type": "done", "content": "Ton contrat se termine le **31 août 2027** [1].", "meta": {"route": "docs", "query": "…", "sources": [...], "warnings": [], "unverified": [], "seconds": 18.4}}
```
➡ Liste complète des événements : [08 — §2](08-generation-reponse.md#2-les-événements-produits).

### `GET /api/chat/{mid}/stream`
Rejoue **tous** les événements depuis le début puis suit les nouveaux. `404` si la réponse est terminée depuis plus d'1 h ou inconnue (il suffit alors de relire la conversation).

### `POST /api/chat/{mid}/stop`
→ `{"stopped": true}` si une génération en cours a été annulée.

---

## 8. Question simple (JSON)

### `POST /api/ask`
```json
{"question": "Quel est mon parcours ?", "mode": "auto"}
```
Réponse (après génération complète, **non enregistrée** dans les conversations, sans historique) :
```json
{
  "ok": true,
  "answer": "Tu as été développeur Python chez ACME à Toulouse de 2019 à 2022 [1]…",
  "route": "docs",
  "sources": [{"n": 1, "name": "cv.pdf", "path": "ajouts/cv.pdf", "page": 1, "cited": true, "excerpt": "…", …}],
  "warnings": [],
  "unverified_numbers": [],
  "seconds": 21.3
}
```
Erreur (Ollama/Qdrant indisponible) : `503` avec `{"ok": false, "answer": "<message d'erreur>", "seconds": …}`.

---

## 9. Exemples d'intégration

**curl, jeton :**
```bash
TOKEN=$(grep ^RAG_API_TOKEN= .env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
     -d '{"question":"Montant de ma dernière facture EDF ?"}' http://localhost:8000/api/ask | jq .answer
```

**Python, streaming :**
```python
import httpx, json
with httpx.stream("POST", "http://localhost:8000/api/chat", auth=("moi", "MOT_DE_PASSE"),
                  json={"message": "Résume mon bail", "mode": "docs"}, timeout=None) as r:
    for line in r.iter_lines():
        ev = json.loads(line)
        if ev["type"] == "token":
            print(ev["text"], end="", flush=True)
        elif ev["type"] in ("done", "error"):
            print("\n", ev.get("meta", {}).get("sources", ev.get("text")))
```

**Synchroniser après avoir copié des fichiers :**
```bash
curl -s -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d '{"wait":true}' \
     http://localhost:8000/api/index | jq .message
```

**Via n8n (webhook public local)** : voir [14 — n8n](14-n8n.md#3-workflow-question-par-webhook).

---

> [⬅ Précédent : 09 — Recherche internet](09-recherche-web.md) · [🏠 Sommaire](README.md) · [Suivant : 11 — Interface web ➡](11-interface-web.md)
