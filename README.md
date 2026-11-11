# Assistant RAG perso, 100 % local

Pose des questions à tes documents (CV, factures, contrats, notes, cours, PDF scannés, photos, tableurs…) et obtiens des réponses **sourcées et vérifiées**. Si l'information n'y est pas, l'assistant peut chercher sur internet via un métamoteur auto-hébergé, en le signalant clairement. **Aucun document ne quitte ta machine.**

> 📚 **Documentation complète** (architecture, schémas, API, configuration, dépannage, glossaire) : [`docs/README.md`](docs/README.md)

```
 PC (hôte)                    Docker
 ┌──────────────┐   ┌──────────────────────────────────────────────────────┐
 │ Ollama       │◄──┤ rag : interface web + API + indexation + OCR         │
 │  gpt-oss:20b │   │   ├─► qdrant  : index vectoriel (sens + mots-clés)   │
 │  bge-m3      │   │   └─► searxng : recherche internet (sans compte)     │
 └──────────────┘   │ n8n : automatisations (API webhook, tâches planif.)  │
                    │ backup : sauvegarde quotidienne                      │
                    └──────────────────────────────────────────────────────┘
```

## Installation (une fois)

Prérequis : Docker (avec `docker compose`) et [Ollama](https://ollama.com) installés sur le PC.

```bash
make install       # configure Ollama (sudo, une fois), télécharge modèles + images, démarre
```

`make install` affiche à la fin l'adresse du chat (**http://localhost:8000**) et les identifiants, qui sont générés dans `.env`.

> **Pourquoi `sudo` ?** Par défaut, Ollama n'écoute que sur `127.0.0.1`, et les conteneurs ne peuvent donc pas le joindre. `make ollama-config` ajoute un réglage systemd (`/etc/systemd/system/ollama.service.d/mon-rag.conf`) qui le fait écouter pour Docker, avec des options d'économie de mémoire. Si le pare-feu `ufw` est actif, seuls les réseaux Docker sont autorisés. Pour annuler, supprime ce fichier puis lance `sudo systemctl daemon-reload && sudo systemctl restart ollama`.

## Au quotidien

| Je veux… | Comment |
|---|---|
| Poser une question | `make open` (chat dans le navigateur) ou `make ask Q="Quel est mon parcours ?"` |
| Ajouter des documents | Onglet **📁 Documents** du chat (glisser-déposer, ou photo depuis le téléphone), ou copie-les dans `documents/` |
| Les rendre consultables | Automatique : le dossier est surveillé toutes les 30 s (seuls les fichiers nouveaux ou modifiés sont relus) |
| Voir l'état | `make status`, `make docs` (fichiers et erreurs de lecture) |
| Diagnostiquer un problème | `make doctor` (teste Ollama, embeddings, LLM, Qdrant, internet, mémoire) |
| Démarrer / arrêter | `make up` / `make down` (données conservées) |

### Ce que fait le chat
- **Réponses en direct** : le texte s'affiche au fur et à mesure. La réponse **continue côté serveur** si tu fermes l'onglet ou si le réseau coupe, et elle est récupérée au retour.
- **Choix automatique de l'outil** (sélecteur 🧭 Auto) : **tes documents** pour ce qui te concerne, **internet** pour l'actualité, la météo ou les prix, **la conversation** pour rédiger, traduire, expliquer ou calculer. Le sélecteur permet de forcer un mode.
- **Questions de suite** : « et là-bas ? » est réécrit en question autonome grâce à l'historique de la conversation.
- **Recherche hybride** : sens de la question (bge-m3) et mots-clés rares (noms, numéros de facture, références), fusionnés. Un filtre de pertinence écarte les passages hors sujet.
- **Sources vérifiables** : chaque information porte un renvoi `[1]` cliquable, qui affiche l'extrait et ouvre le document **à la bonne page**. Les renvois inventés sont retirés et signalés.
- **Chiffres contrôlés** : un montant, une date ou un nombre de la réponse absent des sources est signalé « 🔎 À vérifier ».
- **Internet en renfort** : si tes documents ne contiennent pas la réponse, la question part sur SearXNG, les pages sont lues, nettoyées et filtrées, et la réponse est marquée 🌐 avec ses liens.
- **Anti-injection** : une phrase piégée dans un document ou une page web (« ignore tes instructions… ») est retirée et signalée.
- **Conversations conservées** dans la barre latérale (renommer, supprimer).

| Commande | Effet |
|---|---|
| `/docs` | Liste des documents indexés (et ceux qui n'ont pas pu être lus) |
| `/resume <nom>` | Résumé d'un document entier, même long (ex. `/resume cv`) |
| `/doc <question>` | Uniquement tes documents, jamais internet |
| `/web <question>` | Directement internet |
| `/chat <message>` | Sans recherche (rédaction, traduction…) |
| `/aide` | Aide |

### Formats pris en charge
Sous-dossiers acceptés. Les originaux ne sont jamais modifiés.

| Type | Extensions | Traitement |
|---|---|---|
| PDF | `.pdf` | texte page par page (colonnes respectées) ; pages scannées → OCR automatique |
| Photos, captures | `.jpg` `.png` `.heic` `.webp` `.tiff` `.bmp` `.gif` | redressement, agrandissement, OCR (français + anglais) |
| Word, OpenDocument, etc. | `.docx` `.doc` `.odt` `.rtf` `.epub` `.html` | titres et tableaux conservés |
| Tableurs | `.xlsx` `.xlsm` `.csv` `.tsv` | chaque ligne devient une fiche « colonne : valeur » (5 000 lignes par feuille) |
| Présentations | `.pptx` | texte, tableaux et notes de chaque diapositive |
| Texte, e-mails | `.md` `.txt` `.json` `.yaml` `.xml` `.eml` | lus directement |

## Le modèle de langage

Par défaut : **`gpt-oss:20b`**, un modèle MoE généraliste et bon en français. Seuls 3,6 milliards de ses paramètres travaillent pour chaque mot, ce qui le rend rapide sur CPU. Il occupe environ 14 Go de RAM. Pour en changer, modifie `CHAT_MODEL=…` dans `.env`, puis lance `make models && make up`.

| Modèle | RAM | Remarque |
|---|---|---|
| `gpt-oss:20b` | ~14 Go | défaut, polyvalent |
| `qwen3:30b-a3b` | ~19 Go | MoE très rapide (mets `LLM_THINK=off`) |
| `qwen3:8b` | ~6 Go | pour 16 Go de RAM ou moins (`LLM_THINK=off`) |

Sur CPU, compte 10 à 20 s avant le premier mot, puis 20 à 25 mots par seconde. Le premier message après une pause recharge le modèle (environ 30 s).

## Automatisations avec n8n

n8n (**http://localhost:5678**, identifiants dans `.env`) est **facultatif** : le chat fonctionne sans lui. Deux workflows sont installés et activés :

- **RAG – Question (API webhook)** : pose une question depuis n'importe quel outil (Telegram, e-mail, raccourci iPhone…) :
  ```bash
  curl -u moi:<CHAT_PASSWORD> -X POST http://localhost:5678/webhook/rag-ask \
       -H 'content-type: application/json' -d '{"question": "Quand se termine mon contrat ?"}'
  ```
  La réponse est un JSON : `answer`, `sources`, `route`, `warnings`, `unverified_numbers`.
- **RAG – Vérification nocturne de l'index** : synchronisation complète chaque nuit à 3 h.

Dans tes propres workflows, appelle `http://rag:8000/api/ask` ou `/api/index` avec l'en-tête `Authorization: Bearer {{ $env.RAG_API_TOKEN }}`.

## Sécurité et vie privée
- Tout tourne en local. Seule la **question** (reformulée) part sur internet, et uniquement quand la recherche web est utilisée. `WEB_SEARCH=off` la coupe complètement, et `/doc` la coupe pour une seule question.
- Le chat (port 8000) et n8n (port 5678) n'écoutent que sur `127.0.0.1`. Qdrant et SearXNG ne sont pas exposés.
- Le chat et l'API sont protégés par mot de passe (`CHAT_USER` / `CHAT_PASSWORD`) ou par jeton (`RAG_API_TOKEN`).
- Pages web : seules les adresses publiques sont lues (aucune IP privée ni service interne, y compris après une redirection).
- Le conteneur `rag` tourne avec ton utilisateur, pas en root. Tous les secrets sont aléatoires et stockés dans `.env` (droits 600).

**Accès depuis le téléphone** : dans `.env`, mets `BIND_ADDR=0.0.0.0`, puis lance `make up` et ouvre `http://<IP-du-PC>:8000`. Ne le fais que sur un réseau de confiance, car la connexion est en HTTP.

## Toutes les commandes

| Commande | Rôle |
|---|---|
| `make install` | Première installation complète |
| `make up` / `make down` / `make restart` | Démarrer / arrêter / redémarrer |
| `make open` / `make n8n` | Ouvrir le chat / n8n |
| `make ask Q="…"` / `make web Q="…"` | Question depuis le terminal (documents / internet) |
| `make status` / `make docs` / `make doctor` | État / documents / diagnostic complet |
| `make index` / `make reindex` | Synchroniser maintenant / tout relire |
| `make models` | Télécharger les modèles du `.env` |
| `make ollama-check` / `make ollama-config` | Vérifier / configurer l'Ollama du PC |
| `make logs s=rag` | Logs d'un service (`rag`, `qdrant`, `searxng`, `n8n`) |
| `make backup` / `make restore B=…` | Sauvegarde manuelle / restauration (sauvegarde automatique chaque jour dans `backups/`) |
| `make update` | Mettre à jour les images Docker |
| `make test` | Tests automatiques |
| `make clean` | ⚠ Supprime index, conversations et données n8n (le dossier `documents/` est conservé) |
| `make purge` | ⚠ Efface **toutes** les données perso : index, conversations, n8n, journaux, sauvegardes, documents, `.env` (`GARDER_DOCS=1` garde `documents/`). À lancer avant de publier le projet |

## Réglages (`.env`)
Après modification : `make up`. Si tu changes de modèle d'embeddings, la base est reconstruite automatiquement. Si tu changes `CHUNK_SIZE` ou `CHUNK_OVERLAP`, lance `make reindex`.

| Variable | Défaut | Rôle |
|---|---|---|
| `CHAT_MODEL` | `gpt-oss:20b` | LLM des réponses |
| `EMBED_MODEL` | `bge-m3` | Embeddings multilingues |
| `LLM_THINK` | `low` | Réflexion des modèles « qui pensent » (`off` pour qwen3) |
| `OLLAMA_NUM_CTX` | 16384 | Fenêtre de contexte |
| `TOP_K` | 6 | Extraits donnés au LLM |
| `MIN_SCORE` | 0.40 | Pertinence minimale (monte-la si tu as des réponses hors sujet, baisse-la si « rien trouvé » à tort) |
| `QUERY_ROUTER` | 1 | Choix automatique de l'outil par le LLM (0 = toujours les documents, plus rapide) |
| `MEMORY_TURNS` | 4 | Échanges pris en compte pour les questions de suite |
| `WEB_SEARCH` | `auto` | `off` = jamais d'internet |
| `WEB_RESULTS` | 4 | Pages web lues |
| `INDEX_INTERVAL` | 30 | Surveillance de `documents/` (secondes) |
| `OCR_LANGS` | `fra+eng` | Langues de l'OCR |
| `BIND_ADDR` | `127.0.0.1` | `0.0.0.0` pour un accès depuis le réseau local |

## Dépannage

| Symptôme | Solution |
|---|---|
| « Ollama injoignable » dans le chat ou dans `make doctor` | `make ollama-check` puis, si demandé, `make ollama-config`. Vérifie qu'Ollama tourne : `systemctl status ollama` |
| « modèle absent » | `make models` |
| Réponses très lentes, ou qui n'arrivent pas | Mémoire saturée : ferme des applications ou prends un modèle plus petit. Avec `free -h`, le swap ne doit pas grossir pendant une réponse |
| Un fichier n'est pas trouvé par le chat | `make docs` : il est peut-être illisible (PDF protégé, image sans texte) |
| Réponses hors sujet | Monte `MIN_SCORE` (ex. 0.45) |
| « Rien de pertinent » alors que l'information existe | Baisse `MIN_SCORE` (ex. 0.35), ou reformule avec les mots exacts du document |
| Recherche web vide | `make logs s=searxng` : les moteurs peuvent être bloqués temporairement |

## Structure du projet
```
app/rag/        code Python du service (API, indexation, recherche, chat, interface web)
app/tests/      tests automatiques (make test)
documents/      tes documents (ajouts via l'interface → documents/ajouts/)
n8n/workflows/  workflows importés dans n8n
scripts/        installation, Ollama, sauvegardes
```

## Licence
Distribué sous licence MIT : voir [LICENSE](LICENSE).
