# 20 — Glossaire

> [⬅ Précédent : 19 — Référence du code](19-reference-code.md) · [🏠 Sommaire](README.md)

**Accès rapide :** [A](#a) · [B](#b) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [H](#h) · [I](#i) · [J](#j) · [K](#k) · [L](#l) · [M](#m) · [N](#n) · [O](#o) · [P](#p) · [Q](#q) · [R](#r) · [S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w)

---

### A
**Aiguillage (router)** — Premier appel au LLM qui décide si la question relève des documents (`docs`), d'internet (`web`) ou d'une simple conversation (`chat`), et qui réécrit la question pour qu'elle soit compréhensible seule. ➡ [08 §3](08-generation-reponse.md#3-laiguillage-router)

**API** — *Application Programming Interface*. Ensemble d'adresses HTTP (`/api/…`) permettant à un programme de piloter l'assistant. ➡ [10](10-api.md)

**asyncio** — Bibliothèque Python de programmation asynchrone : permet au service de gérer plusieurs requêtes en même temps sans threads.

### B
**Basic (authentification HTTP)** — Méthode où le navigateur envoie identifiant et mot de passe (encodés en base64) à chaque requête. Utilisée par le chat.

**Bearer (jeton)** — Méthode d'authentification par un secret unique dans l'en-tête `Authorization: Bearer …`. Utilisée par n8n, la CLI et les scripts (`RAG_API_TOKEN`).

**bge-m3** — Modèle d'*embeddings* multilingue (BAAI) produisant des vecteurs de 1024 dimensions. Sert à représenter le **sens** des morceaux et des questions.

### C
**Chunk / morceau** — Portion de texte d'environ 1 200 caractères issue du découpage d'un document. C'est l'unité stockée et recherchée. ➡ [05 §5](05-indexation.md#5-le-découpage-en-morceaux-chunking)

**Chevauchement (overlap)** — Partie de texte répétée entre deux morceaux consécutifs (≈ 200 caractères) pour ne pas couper une information à la frontière.

**Citation `[n]`** — Renvoi dans la réponse vers l'extrait n° *n*. Vérifié après génération. ➡ [08 §7](08-generation-reponse.md#7-vérifications-après-génération)

**CLI** — *Command Line Interface*. Ici `rag/cli.py`, appelé par `make status`, `make ask`…

**Collection** — Ensemble de points dans Qdrant (ici `documents`), équivalent d'une table.

**Cosinus (similarité)** — Mesure de proximité entre deux vecteurs (de −1 à 1, en pratique 0 à 1 ici). Plus elle est haute, plus les textes ont un sens proche.

**CSP** — *Content Security Policy*. En-tête HTTP qui limite ce qu'une page a le droit de charger ou d'exécuter. ➡ [13 §6](13-securite.md#6-fichiers--traversée-de-chemin-html-piégé)

**Contexte (fenêtre de)** — Quantité maximale de texte (en *tokens*) que le LLM peut lire en une fois (`OLLAMA_NUM_CTX`, 16 384).

### D
**Dense (vecteur)** — Vecteur où toutes les dimensions ont une valeur (ex. 1024 nombres) ; produit par un modèle d'embeddings, capture le sens.

**Docker / Docker Compose** — Outil qui fait tourner des applications isolées (*conteneurs*) ; Compose décrit plusieurs conteneurs et leurs liens dans `docker-compose.yml`.

**Drop-in systemd** — Fichier de configuration complémentaire d'un service système (`/etc/systemd/system/ollama.service.d/mon-rag.conf`), qui modifie Ollama sans toucher à son fichier d'origine.

### E
**Embedding** — Représentation d'un texte sous forme de vecteur de nombres, telle que des textes de sens proche ont des vecteurs proches.

**Extrait / passage** — Morceau retrouvé par la recherche et fourni au LLM, entre balises `<extrait>`.

### F
**FastAPI** — Framework web Python utilisé pour l'API et l'interface du service `rag`.

**Fusion RRF** — Voir *RRF*.

### H
**Hallucination** — Information inventée par un LLM et présentée comme vraie. Combattue par les règles du prompt, les citations vérifiées et le contrôle des chiffres.

**Healthcheck** — Test périodique qui indique à Docker si un conteneur fonctionne (`/api/health` pour `rag`, `/healthz` pour n8n).

**host.docker.internal** — Nom qui désigne, depuis un conteneur, la machine hôte. Permet au service `rag` de joindre Ollama.

**Hybride (recherche)** — Combinaison d'une recherche par sens (dense) et d'une recherche par mots-clés (creuse). ➡ [07](07-recherche-hybride.md)

### I
**IDF** — *Inverse Document Frequency*. Poids d'un mot d'autant plus élevé qu'il est rare dans l'ensemble des documents. Calculé par Qdrant pour les vecteurs creux.

**Idempotent** — Se dit d'une opération qui donne le même résultat si on la répète (réindexer un fichier inchangé réécrit exactement les mêmes points).

**Indexation** — Transformation des documents en morceaux vectorisés stockés dans Qdrant. ➡ [05](05-indexation.md)

**Injection de prompt** — Texte placé dans un document ou une page web pour détourner les consignes du LLM. ➡ [13 §4](13-securite.md#4-injection-de-prompt)

### J
**Job** — Tâche de fond qui produit une réponse, indépendante de la connexion du navigateur. ➡ [08 §11](08-generation-reponse.md#11-tâches-de-fond-jobspy)

### K
**Keep-alive (Ollama)** — Durée pendant laquelle Ollama garde un modèle chargé en mémoire après usage (`OLLAMA_KEEP_ALIVE`, 30 min).

**KV cache** — Mémoire de travail du LLM pendant la génération. `OLLAMA_KV_CACHE_TYPE=q8_0` la compresse (≈ 2× moins de RAM).

### L
**LLM** — *Large Language Model*, grand modèle de langage. Ici `gpt-oss:20b` servi par Ollama.

**Lifespan** — Fonction FastAPI exécutée au démarrage et à l'arrêt du service (création des clients, lancement de la surveillance).

### M
**Markdown** — Syntaxe de mise en forme légère (`**gras**`, `# titre`, `- liste`, tableaux `| … |`). Utilisée par le LLM et rendue par l'interface.

**Métamoteur** — Moteur de recherche qui interroge d'autres moteurs et agrège leurs résultats (SearXNG).

**MoE** — *Mixture of Experts*. Architecture de LLM où seule une partie des paramètres travaille pour chaque mot (3,6 Md sur 21 Md pour gpt-oss:20b) → beaucoup plus rapide sur CPU.

### N
**n8n** — Outil d'automatisation visuel auto-hébergé. ➡ [14](14-n8n.md)

**NDJSON** — *Newline-Delimited JSON*. Flux où chaque ligne est un objet JSON complet ; utilisé pour diffuser les événements de réponse.

**NON_TROUVE (sentinelle)** — Mot réservé que le LLM doit écrire seul quand les sources ne permettent pas de répondre ; il déclenche la bascule vers internet et n'est jamais affiché. ➡ [08 §8](08-generation-reponse.md#8-streaming-et-sentinelle-non_trouve)

### O
**OCR** — *Optical Character Recognition*, reconnaissance de texte dans une image (Tesseract). ➡ [06 §4](06-extraction.md#4-images-et-ocr)

**Ollama** — Logiciel qui télécharge et exécute des LLM et modèles d'embeddings en local, via une API HTTP (port 11434).

### P
**Payload** — Données attachées à un point Qdrant (chemin, page, texte du morceau…).

**Point** — Élément stocké dans Qdrant : un identifiant, des vecteurs, un payload. Ici, un point = un morceau.

**Prompt (système)** — Consignes données au LLM avant la question (rôle, règles, format). ➡ [08 §10](08-generation-reponse.md#10-les-prompts-système)

### Q
**Qdrant** — Base de données vectorielle open source, qui stocke les morceaux et répond aux recherches par similarité.

### R
**RAG** — *Retrieval-Augmented Generation*. Technique qui consiste à **rechercher** des passages pertinents puis à les fournir au LLM pour qu'il **génère** une réponse fondée sur eux.

**Réindexation complète** — Suppression de l'index puis relecture de tous les documents (`make reindex`).

**RRF** — *Reciprocal Rank Fusion*. Méthode de fusion de plusieurs classements : score = Σ 1/(60 + rang). ➡ [07 §4](07-recherche-hybride.md#4-fusion-rrf)

### S
**SearXNG** — Métamoteur de recherche libre et auto-hébergé. ➡ [09](09-recherche-web.md)

**Sémaphore** — Mécanisme qui limite le nombre de tâches simultanées (ici 1 génération à la fois).

**SHA-256** — Empreinte cryptographique d'un fichier ; change dès qu'un octet change. Sert à détecter les modifications réelles.

**Sparse (vecteur creux)** — Vecteur dont presque toutes les dimensions valent 0 ; seules celles correspondant aux mots présents ont une valeur. Sert à la recherche par mots-clés.

**SQLite** — Base de données dans un simple fichier (`rag.sqlite3`) : conversations et état de l'index.

**SSRF** — *Server-Side Request Forgery*. Attaque qui fait envoyer au serveur des requêtes vers des services internes. ➡ [13 §5](13-securite.md#5-ssrf-lecture-de-pages-web)

**Stopwords (mots vides)** — Mots trop fréquents pour être utiles à la recherche (le, de, the, of…), ignorés dans les vecteurs creux.

**Streaming** — Diffusion de la réponse au fur et à mesure de sa génération.

### T
**Température** — Paramètre du LLM réglant la part d'aléatoire : basse (0,2) = réponses factuelles et stables.

**Tesseract** — Moteur d'OCR libre, utilisé avec les langues française et anglaise.

**Thinking (réflexion)** — Phase de raisonnement interne de certains modèles (gpt-oss, qwen3) avant la réponse ; jamais affichée, signalée par « (réflexion du modèle) ».

**Token** — Unité de texte manipulée par le LLM (≈ ¾ de mot ; ≈ 2,6 caractères en français dans ce projet).

**TOP_K** — Nombre de passages retenus et fournis au LLM (6).

**trafilatura** — Bibliothèque Python qui extrait le texte principal d'une page web (sans menus ni publicités).

### U
**Upsert** — Opération « insérer ou remplacer » dans Qdrant.

**UUID5** — Identifiant déterministe calculé à partir d'un texte (chemin + SHA + n° de morceau) : le même morceau a toujours le même ID.

### V
**Vecteur** — Liste de nombres représentant un texte dans un espace mathématique.

**Volume Docker** — Espace de stockage persistant géré par Docker, qui survit à la suppression des conteneurs (`rag_state`, `qdrant_data`, `n8n_data`).

### W
**WAL** — *Write-Ahead Logging*. Mode de SQLite permettant des lectures pendant une écriture.

**Webhook** — Adresse HTTP qui déclenche un workflow quand on l'appelle (`/webhook/rag-ask`).

**Workflow** — Enchaînement de nœuds dans n8n.

---

<div align="center">

**Fin de la documentation**

[⬅ Précédent : 19 — Référence du code](19-reference-code.md) · [🏠 Retour au sommaire](README.md)

</div>
