"""Anbindung an lokale Modell-Server.

Zwei Arten: Ollama (eigene API, kann denken/num_ctx/keep_alive) und alles mit
OpenAI-kompatibler API (LM Studio u.a.). Welche Server gefragt werden, steht in
der Config unter "server". Die Modellauswahl zeigt die Modelle aller gerade
erreichbaren Server -- wer Ollama hat, sieht Ollama, wer LM Studio hat, LM Studio.

Jeder Server bekommt die Nachrichten in seinem eigenen Format zurueck, so wie er
sie geliefert hat; umgerechnet wird nichts.
"""
from concurrent.futures import ThreadPoolExecutor

import requests

import prompts

TRENNER = " :: "   # Modell-Kennung im UI: "<Servername> :: <Modell>"


def server_liste(cfg=None):
    cfg = cfg or prompts.load()
    return [s for s in cfg.get("server", []) if s.get("an", True) and s.get("url")]


def _modelle_von(s):
    url = s["url"].rstrip("/")
    try:
        if s.get("typ") == "ollama":
            r = requests.get(url + "/api/tags", timeout=3)
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]
        r = requests.get(url + "/v1/models", timeout=3)
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]
    except Exception:
        return []


def models():
    sv = server_liste()
    if not sv:
        return []
    with ThreadPoolExecutor(len(sv)) as ex:
        res = list(ex.map(_modelle_von, sv))
    return [s["name"] + TRENNER + m for s, ms in zip(sv, res) for m in ms]


def _aufloesen(model, cfg):
    name, _, m = model.partition(TRENNER)
    for s in server_liste(cfg):
        if s["name"] == name:
            return s, m
    raise ValueError("Server '%s' steht nicht (mehr) in den Einstellungen" % name)


def chat(model, messages, tools=None):
    """Eine Antwort holen. Rueckgabe: die Assistenten-Nachricht im Format des
    Servers (tool_calls[].function.arguments ist bei Ollama ein dict, bei
    OpenAI-kompatiblen ein JSON-String)."""
    cfg = prompts.load()
    s, m = _aufloesen(model, cfg)
    url = s["url"].rstrip("/")
    if s.get("typ") == "ollama":
        body = {"model": m, "messages": messages, "stream": False,
                "think": bool(cfg.get("think", True)),
                "keep_alive": cfg.get("keep_alive", "30m"),
                "options": {"num_ctx": int(cfg.get("num_ctx", 16384))}}
        if tools:
            body["tools"] = tools
        r = requests.post(url + "/api/chat", json=body, timeout=600)
        r.raise_for_status()
        return r.json()["message"]
    body = {"model": m, "messages": messages, "stream": False}
    if tools:
        body["tools"] = tools
    r = requests.post(url + "/v1/chat/completions", json=body, timeout=600)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]
