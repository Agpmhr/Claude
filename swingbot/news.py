"""Lecteur de news par IA : uniquement en DÉFENSE.

Actions possibles : bloquer les nouvelles entrées, réduire la taille, resserrer ou couper une position exposée.
Action INTERDITE : ouvrir un trade à cause d'une news (garanti par le code : ce module ne renvoie jamais d'ordre d'achat/vente).
Toutes les décisions sont journalisées dans logs/news_decisions.jsonl avec la news qui les a déclenchées.

Ce module NE PEUT PAS être backtesté honnêtement (pas d'historique de titres sans biais du futur) :
il sera jugé pendant la période de démo.

Modèle de langage interchangeable :
  NEWS_LLM=ollama     -> LLM local (http://localhost:11434), modèle OLLAMA_MODEL (défaut : llama3.1)
  NEWS_LLM=anthropic  -> API Anthropic, clé dans ANTHROPIC_API_KEY, modèle NEWS_MODEL (défaut : claude-haiku-5-5)
  NEWS_LLM=none       -> seulement le calendrier (règles fixes, sans LLM)
Usage : python -m swingbot.news
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paths import ROOT  # noqa: E402
LOG = ROOT / "logs" / "news_decisions.jsonl"

FEEDS = {
    "calendrier_semaine": "https://nfs.faireconomy.media/ff_calendar_thisweek.xml",
    "fxstreet": "https://www.fxstreet.com/rss/news",
    "investing_forex": "https://www.investing.com/rss/news_1.rss",
    "fed": "https://www.federalreserve.gov/feeds/press_all.xml",
    "ecb": "https://www.ecb.europa.eu/rss/press.html",
}
CCYS = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "XAU", "EQ"]
ALLOWED = {"aucune", "bloquer_entrees", "reduire_taille", "resserrer_stop", "couper_position"}


@dataclass
class Decision:
    time: str
    devise: str
    niveau: int            # 0 = rien, 1 = attention, 2 = élevé, 3 = critique
    action: str            # une des ALLOWED
    raison: str
    source: str
    titre: str


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 swingbot-news"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def read_calendar(hours_ahead: int = 24) -> list[dict]:
    """Annonces à fort impact des prochaines heures (calendrier ForexFactory de la semaine)."""
    out = []
    try:
        root = ET.fromstring(_get(FEEDS["calendrier_semaine"]))
    except Exception as e:
        return [dict(erreur=str(e))]
    now = datetime.now(timezone.utc)
    for ev in root.iter("event"):
        g = {c.tag: (c.text or "").strip() for c in ev}
        if g.get("impact") != "High":
            continue
        try:
            # le flux est en heure de New York (approximation : UTC-5)
            t = datetime.strptime(f"{g['date']} {g['time']}", "%m-%d-%Y %I:%M%p").replace(tzinfo=timezone.utc) \
                + timedelta(hours=5)
        except Exception:
            continue
        if now - timedelta(hours=1) <= t <= now + timedelta(hours=hours_ahead):
            out.append(dict(devise=g.get("country"), titre=g.get("title"), heure_utc=t.isoformat()))
    return out


def read_headlines(max_items: int = 40) -> list[dict]:
    items = []
    for name, url in FEEDS.items():
        if name == "calendrier_semaine":
            continue
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for it in list(root.iter("item"))[:15]:
            items.append(dict(source=name, titre=(it.findtext("title") or "").strip(),
                              date=(it.findtext("pubDate") or "").strip()))
    return items[:max_items]


PROMPT = """Tu es le module de DÉFENSE d'un robot de trading. Tu ne proposes JAMAIS d'ouvrir une position.
Pour chaque titre ci-dessous, évalue le risque à court terme (prochaines 24 h) pour les devises/actifs :
{ccys}. Réponds UNIQUEMENT en JSON : une liste d'objets
{{"devise": "...", "niveau": 0-3, "action": "aucune|bloquer_entrees|reduire_taille|resserrer_stop|couper_position",
  "raison": "phrase courte", "index_titre": n}}
Ne liste que les devises réellement concernées (niveau >= 2). « couper_position » seulement pour un choc majeur
(intervention de banque centrale surprise, guerre, défaut, krach en cours).
Titres :
{titles}"""


def classify_llm(headlines: list[dict]) -> list[dict]:
    backend = os.environ.get("NEWS_LLM", "none").lower()
    if backend == "none" or not headlines:
        return []
    titles = "\n".join(f"{i}. [{h['source']}] {h['titre']}" for i, h in enumerate(headlines))
    prompt = PROMPT.format(ccys=", ".join(CCYS), titles=titles)
    if backend == "ollama":
        body = json.dumps(dict(model=os.environ.get("OLLAMA_MODEL", "llama3.1"), prompt=prompt, stream=False,
                               format="json")).encode()
        req = urllib.request.Request("http://localhost:11434/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        text = json.loads(urllib.request.urlopen(req, timeout=120).read())["response"]
    elif backend == "anthropic":
        key = os.environ["ANTHROPIC_API_KEY"]          # jamais écrite dans le code ni dans Git
        body = json.dumps(dict(model=os.environ.get("NEWS_MODEL", "claude-haiku-5-5"), max_tokens=1500,
                               messages=[dict(role="user", content=prompt)])).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        text = json.loads(urllib.request.urlopen(req, timeout=120).read())["content"][0]["text"]
    else:
        raise ValueError(f"NEWS_LLM inconnu : {backend}")
    m = re.search(r"\[.*\]", text, re.S)
    try:
        data = json.loads(m.group(0) if m else text)
    except Exception:
        return []
    return data if isinstance(data, list) else data.get("items", [])


def assess(block_hours_before: int = 2) -> list[Decision]:
    """Décisions défensives du moment. Les règles calendrier sont fixes ; le LLM ne peut qu'ajouter de la prudence."""
    now = datetime.now(timezone.utc).isoformat()
    dec: list[Decision] = []
    for ev in read_calendar():
        if "erreur" in ev:
            continue
        t = datetime.fromisoformat(ev["heure_utc"])
        if t - datetime.now(timezone.utc) <= timedelta(hours=block_hours_before):
            dec.append(Decision(now, ev["devise"], 2, "bloquer_entrees",
                                f"annonce majeure à {ev['heure_utc']}", "calendrier", ev["titre"]))
    heads = read_headlines()
    for d in classify_llm(heads):
        act = d.get("action", "aucune")
        if act not in ALLOWED:          # sécurité : toute autre action (ex. « acheter ») est ignorée
            continue
        i = d.get("index_titre", -1)
        h = heads[i] if isinstance(i, int) and 0 <= i < len(heads) else dict(source="?", titre="?")
        dec.append(Decision(now, str(d.get("devise", "?")), int(d.get("niveau", 0)), act,
                            str(d.get("raison", ""))[:200], h["source"], h["titre"]))
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        for d in dec:
            f.write(json.dumps(asdict(d), ensure_ascii=False) + "\n")
    return dec


def restrictions(decisions: list[Decision]) -> dict[str, str]:
    """Résumé par devise : l'action la plus prudente demandée."""
    order = ["aucune", "resserrer_stop", "reduire_taille", "bloquer_entrees", "couper_position"]
    out: dict[str, str] = {}
    for d in decisions:
        if order.index(d.action) > order.index(out.get(d.devise, "aucune")):
            out[d.devise] = d.action
    return out


if __name__ == "__main__":
    ds = assess()
    for d in ds:
        print(asdict(d))
    print("Restrictions :", restrictions(ds))
