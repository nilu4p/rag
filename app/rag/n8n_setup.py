"""Configuration de n8n (lancé par `make up`) : compte propriétaire + activation des workflows du projet.

n8n est facultatif pour le RAG : il sert aux automatisations (API par webhook, tâches planifiées…).
"""
from __future__ import annotations

import os
import sys
import time

import httpx

N8N = os.environ.get("N8N_INTERNAL_URL", "http://n8n:5678").rstrip("/")
EMAIL = os.environ.get("N8N_OWNER_EMAIL", "admin@rag.local")
PASSWORD = os.environ.get("N8N_OWNER_PASSWORD", "")
WORKFLOWS = {"ragAskWebhook01": "/webhook/rag-ask", "ragNightlySync1": None}
G, Y, R, X = "\033[32m", "\033[33m", "\033[31m", "\033[0m"


def main() -> None:
    c = httpx.Client(base_url=N8N, timeout=30, headers={"browser-id": "rag-setup"})
    for _ in range(90):
        try:
            if c.get("/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    else:
        print(f"{Y}⚠ n8n ne répond pas : automatisations non configurées (le chat fonctionne quand même){X}")
        return

    s = c.get("/rest/settings").json().get("data", {})
    if s.get("userManagement", {}).get("showSetupOnFirstLoad"):
        r = c.post("/rest/owner/setup", json={"email": EMAIL, "firstName": "Admin", "lastName": "RAG", "password": PASSWORD})
        print(f"{G}✔{X} Compte n8n créé ({EMAIL})" if r.is_success else f"{Y}⚠ Création du compte n8n : {r.status_code} {r.text[:200]}{X}")
    r = c.post("/rest/login", json={"emailOrLdapLoginId": EMAIL, "email": EMAIL, "password": PASSWORD})
    if not r.is_success:
        print(f"{Y}⚠ Connexion à n8n impossible avec les identifiants du .env (compte créé à la main ?).{X}")
        print("  Active toi-même les workflows « RAG – … » dans n8n.")
        return

    for wid, path in WORKFLOWS.items():
        g = c.get(f"/rest/workflows/{wid}")
        if g.status_code == 404:
            continue
        wf = g.json().get("data", {})
        if wf.get("active"):
            continue
        version = wf.get("versionId")
        r = c.post(f"/rest/workflows/{wid}/activate", json={"versionId": version})
        if not r.is_success:
            r = c.patch(f"/rest/workflows/{wid}", json={"active": True, "versionId": version})
        name = wf.get("name", wid)
        print(f"{G}✔{X} Workflow activé : {name}" if r.is_success else f"{Y}⚠ Active « {name} » dans n8n ({r.status_code}){X}")
    print(f"{G}✔{X} n8n prêt")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - n8n est facultatif : ne jamais bloquer make up
        print(f"{Y}⚠ Configuration de n8n incomplète : {e}{X}", file=sys.stderr)
