"""HTTP-Schicht. Haelt genau eine offene Session -- das Werkzeug ist fuer
einen Menschen an einem Rechner, nicht fuer mehrere."""
import threading
import traceback
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, discovery, ops, prompts
from .proposals import APPROVED, DECLINED, Store
from .session import Session

WEB = Path(__file__).resolve().parent.parent / "web"
app = FastAPI(title="claudeJaja")


class State:
    sess = None
    store = Store()
    purge = None
    agent_lauf = {"aktiv": False, "events": [], "ergebnis": None}


S = State()


def need():
    if S.sess is None:
        raise HTTPException(400, "keine Session offen")
    return S.sess


# ------------------------------------------------------------------ modelle
class OpenReq(BaseModel):
    file: str


class IdxReq(BaseModel):
    idxs: list


class RewriteReq(BaseModel):
    idx: int
    text: str


class AgentReq(BaseModel):
    auftrag: str
    model: str


class DecideReq(BaseModel):
    pid: int
    status: str
    note: str = ""


# ------------------------------------------------------------------ routen
@app.get("/api/tree")
def r_tree():
    return discovery.tree()


@app.get("/api/models")
def r_models():
    return {"models": agent.models()}


@app.post("/api/open")
def r_open(q: OpenReq):
    p = Path(q.file)
    if not p.exists():
        raise HTTPException(404, "Datei nicht gefunden")
    S.sess = Session(p)
    S.store = Store()
    S.agent_lauf = {"aktiv": False, "events": [], "ergebnis": None}
    # Vorarbeit: Sicherung anlegen, Denkprozesse sofort raus -- nur im
    # Arbeitsmodell. Die Datei wird erst beim Speichern angefasst, damit eine
    # gerade laufende Session nicht zerschossen wird.
    bak = S.sess.backup()
    S.purge = ops.purge_thinking(S.sess)
    return {"stats": S.sess.stats(), "purge": S.purge, "backup": bak,
            "path": str(p), "titel": p.stem}


@app.get("/api/items")
def r_items():
    s = need()
    _, probleme = ops.preview(s)
    return {"items": s.view(), "stats": s.stats(), "purge": S.purge,
            "probleme": probleme, "proposals": S.store.view(),
            "log": S.store.log[-20:], "dirty": s.dirty}


@app.post("/api/delete")
def r_delete(q: IdxReq):
    try:
        return ops.delete(need(), q.idxs)
    except ops.OpError as e:
        raise HTTPException(400, str(e))


@app.post("/api/restore")
def r_restore(q: IdxReq):
    return {"wiederhergestellt": ops.restore(need(), q.idxs)}


@app.post("/api/rewrite")
def r_rewrite(q: RewriteReq):
    try:
        return ops.rewrite(need(), q.idx, q.text)
    except ops.OpError as e:
        raise HTTPException(400, str(e))


@app.post("/api/revert")
def r_revert(q: RewriteReq):
    return {"ok": ops.revert(need(), q.idx)}


@app.post("/api/proposal/decide")
def r_decide(q: DecideReq):
    s = need()
    p, stale = S.store.decide(q.pid, q.status, q.note)
    if p is None:
        raise HTTPException(404, "Vorschlag nicht gefunden")
    angewandt = None
    if q.status == APPROVED:
        try:
            if p.kind == "delete":
                angewandt = ops.delete(s, p.idxs)
            elif p.kind == "rewrite":
                angewandt = ops.rewrite(s, p.idx, p.new_text)
            elif p.kind == "switch_model":
                angewandt = {"hinweis": "Empfehlung -- nichts an der Datei geaendert"}
        except ops.OpError as e:
            p.status = "offen"
            raise HTTPException(400, str(e))
    return {"vorschlag": q.pid, "status": p.status,
            "hinfaellig": stale, "angewandt": angewandt}


@app.post("/api/agent/run")
def r_agent(q: AgentReq):
    s = need()
    if S.agent_lauf["aktiv"]:
        raise HTTPException(409, "Agent laeuft bereits")
    S.store.reset_open()
    S.agent_lauf = {"aktiv": True, "events": [], "ergebnis": None}

    def ev(e):
        S.agent_lauf["events"].append(e)

    def lauf():
        try:
            r = agent.Runner(s, S.store, q.model, on_event=ev).run(q.auftrag)
            S.agent_lauf["ergebnis"] = r
        except Exception as e:
            S.agent_lauf["ergebnis"] = {"ok": False, "fehler": str(e)}
            ev({"typ": "fehler", "text": traceback.format_exc()[-400:]})
        finally:
            S.agent_lauf["aktiv"] = False

    threading.Thread(target=lauf, daemon=True).start()
    return {"gestartet": True}


@app.get("/api/agent/status")
def r_agent_status():
    return {**S.agent_lauf, "proposals": S.store.view()}


@app.post("/api/save")
def r_save():
    s = need()
    try:
        res = ops.save(s)
    except ops.OpError as e:
        raise HTTPException(400, str(e))
    S.purge = ops.purge_thinking(s)   # nach Reload wieder sauber halten
    S.store = Store()
    return {**res, "stats": s.stats()}


class PromptsReq(BaseModel):
    data: dict


@app.get("/api/prompts")
def r_prompts_get():
    return prompts.load()


@app.post("/api/prompts")
def r_prompts_set(q: PromptsReq):
    prompts.save(q.data)
    return {"ok": True}


class ChatReq(BaseModel):
    model: str
    messages: list


@app.post("/api/chat/send")
def r_chat(q: ChatReq):
    """Reiner Chat mit dem lokalen Modell -- kein Systemprompt, keine Werkzeuge.
    Unabhaengig von einer offenen Session."""
    import requests
    cfg = prompts.load()
    try:
        r = requests.post(agent.OLLAMA + "/api/chat", timeout=600, json={
            "model": q.model, "messages": q.messages, "stream": False,
            "think": bool(cfg.get("think", True)),
            "keep_alive": cfg.get("keep_alive", "30m"),
            "options": {"num_ctx": int(cfg.get("num_ctx", 16384))}})
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(502, "Ollama-Fehler: %s" % e)
    return {"reply": r.json().get("message", {}).get("content", "")}


@app.get("/")
def r_index():
    return FileResponse(WEB / "index.html")


app.mount("/web", StaticFiles(directory=str(WEB)), name="web")
