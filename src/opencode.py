"""opencode als zweite Quelle.

opencode legt NICHT eine Datei je Session an, sondern eine einzige SQLite-DB mit
allen Sessions. Form (nur das, was hier zaehlt):

  session_v2        eine Zeile je Session: id, directory, title, model, ...
  session_message   eine Zeile je Nachricht: session_id, seq, type, data(JSON)

Die Reihenfolge steckt in 'seq' (ganzzahlig), nicht in einer uuid-Kette -- beim
Loeschen muss also nichts neu verkettet werden. Ein Werkzeugaufruf und sein
Ergebnis stehen in EINEM 'tool'-Block (state.input / state.content), nicht in
zwei Records; nach aussen zeigen wir sie wie bei Claude als tool_use + tool_result,
beim Speichern gehen sie wieder in den einen Block zurueck.

Lage: Ich sitze am PC, opencode ist geschlossen (oder ich arbeite bewusst auf
einer Kopie), oeffne gaslight im Browser, waehle die Quelle "opencode", raeume
eine Session auf und speichere. Beim Speichern wird die DB zuerst gesichert und
dann die echte DB beschrieben -- lesen geht auch bei offenem opencode, schreiben
NICHT, darum muss opencode dafuer zu sein.

Der DB-Pfad wird portabel aus dem Home-Verzeichnis gebildet (XDG-konform), nicht
hartkodiert; zum Testen laesst er sich ueber GASLIGHT_OPENCODE_DB umbiegen.
"""
import json
import os
import sqlite3
import time
from collections import defaultdict
from pathlib import Path

import tokens
from session import Item, Session, _result_text


def db_path():
    env = os.environ.get("GASLIGHT_OPENCODE_DB")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "opencode" / "opencode.db"


def _connect_ro(db):
    # mode=ro respektiert WAL und blockiert ein laufendes opencode nicht beim Lesen.
    return sqlite3.connect("file:%s?mode=ro" % Path(db).as_posix(), uri=True, timeout=5)


def sessions():
    """Alle Sessions als flache Liste, gleiche Felder wie discovery.sessions()."""
    db = db_path()
    if not db.exists():
        return []
    try:
        con = _connect_ro(db)
    except sqlite3.Error:
        return []
    try:
        rows = con.execute(
            "SELECT s.id, s.directory, s.title, s.model, s.time_updated, "
            "(SELECT count(*) FROM session_message m WHERE m.session_id = s.id) "
            "FROM session_v2 s WHERE s.time_archived IS NULL"
        ).fetchall()
    finally:
        con.close()
    out = []
    for sid, directory, title, model, tup, cnt in rows:
        out.append({
            "id": sid,
            "file": str(db),
            "source": "opencode",
            "cwd": directory or "",
            "title": title or sid,
            "size": cnt,
            "mtime": (tup or 0) / 1000.0,
            "model": model,
        })
    out.sort(key=lambda s: s["mtime"], reverse=True)
    return out


class OpencodeSession(Session):
    """Eine opencode-Session mit derselben Item-Schnittstelle wie Session.

    records sind hier die session_message-Zeilen: {id, type, seq, data(dict)}.
    """

    def __init__(self, db, session_id, model=None):
        self.db = str(db)
        self.sid = session_id
        self._model = model
        super().__init__(db)      # ruft self.load() -> self._build_items()
        self.source = "opencode"  # nach super().__init__, das setzt "claude"

    def load(self):
        self.records, self.items = [], []
        con = sqlite3.connect(self.db, timeout=5)
        try:
            rows = con.execute(
                "SELECT id, type, seq, data FROM session_message "
                "WHERE session_id = ? ORDER BY seq", (self.sid,)).fetchall()
        finally:
            con.close()
        for mid, mtype, seq, data in rows:
            try:
                d = json.loads(data)
            except Exception:
                d = {"__unparsed__": data}
            self.records.append({"id": mid, "type": mtype, "seq": seq, "data": d})
        self._build_items()

    def _build_items(self):
        self.items = []
        n = 0
        for ri, rec in enumerate(self.records):
            t, d = rec["type"], rec["data"]
            if t == "user":
                txt = d.get("text") or (d.get("metadata") or {}).get("displayText") or ""
                self.items.append(Item(n, ri, -1, "user", "text", txt,
                                       editable=True, tokens=tokens.count(txt)))
                n += 1
            elif t == "assistant":
                for bi, b in enumerate(d.get("content") or []):
                    if not isinstance(b, dict):
                        continue
                    bt = b.get("type")
                    if bt == "reasoning":
                        txt = b.get("text", "") or ""
                        self.items.append(Item(n, ri, bi, "assistant", "thinking", txt,
                                               editable=bool(txt), tokens=tokens.count(txt),
                                               sub="reasoning"))
                        n += 1
                    elif bt == "text":
                        txt = b.get("text", "") or ""
                        self.items.append(Item(n, ri, bi, "assistant", "text", txt,
                                               editable=True, tokens=tokens.count(txt),
                                               sub="text"))
                        n += 1
                    elif bt == "tool":
                        st = b.get("state") or {}
                        tid, name = b.get("id"), b.get("name")
                        inp = json.dumps(st.get("input", {}), ensure_ascii=False, indent=2)
                        self.items.append(Item(n, ri, bi, "assistant", "tool_use", inp,
                                               name=name, tool_id=tid, editable=True,
                                               tokens=tokens.count(inp), sub="input"))
                        n += 1
                        out, ed = _result_text(st.get("content"))
                        self.items.append(Item(n, ri, bi, "user", "tool_result", out,
                                               name=name, tool_id=tid, editable=ed,
                                               tokens=tokens.count(out), sub="output"))
                        n += 1
            else:
                # system / idle / sonstiges -> nur anzeigen und loeschbar, nicht editierbar
                txt = d.get("text") or ("[%s]" % t)
                body = json.dumps(d, ensure_ascii=False)
                self.items.append(Item(n, ri, -1, "meta", "meta", txt[:400],
                                       meta_type=t, tokens=tokens.count(body)))
                n += 1

    def active_model(self):
        raw = self._model
        if raw:
            try:
                return json.loads(raw).get("id")
            except Exception:
                return raw
        for rec in reversed(self.records):
            if rec["type"] == "assistant":
                m = rec["data"].get("model")
                if isinstance(m, dict) and m.get("id"):
                    return m["id"]
        return None


def save(sess):
    """Edits/Loeschungen aus dem Item-Modell in die DB zurueckschreiben.

    Reihenfolge passt zur Lage: zuerst sichern (Kopie der ganzen DB), dann die
    echte DB beschreiben. Gaps in 'seq' durch geloeschte Zeilen sind unkritisch,
    opencode liest nach seq-Reihenfolge, nicht nach luckenloser Nummer.
    """
    bak = sess.backup()
    drop_rows, updates = [], {}
    by_rec = defaultdict(list)
    for it in sess.items:
        by_rec[it.rec].append(it)

    for ri, rec in enumerate(sess.records):
        rid, t, d = rec["id"], rec["type"], rec["data"]
        its = by_rec.get(ri, [])
        if t == "user":
            it = its[0]
            if it.deleted:
                drop_rows.append(rid)
            elif it.edited:
                nd = json.loads(json.dumps(d, ensure_ascii=False))
                nd["text"] = it.text
                if isinstance(nd.get("metadata"), dict):
                    nd["metadata"]["displayText"] = it.text
                updates[rid] = nd
        elif t == "assistant":
            nd = json.loads(json.dumps(d, ensure_ascii=False))
            content = nd.get("content") or []
            bymap = defaultdict(list)
            for it in its:
                bymap[it.bi].append(it)
            keep, changed = [], False
            for bi, b in enumerate(content):
                parts = bymap.get(bi)
                if not parts:          # Blocktyp, den wir nicht zeigen -> unveraendert behalten
                    keep.append(b)
                    continue
                if any(p.deleted for p in parts):
                    changed = True     # ganzen Block weglassen (Tool-Paar geht zusammen)
                    continue
                for p in parts:
                    if not p.edited:
                        continue
                    changed = True
                    if p.kind == "tool_use":
                        b.setdefault("state", {})["input"] = json.loads(p.text)
                    elif p.kind == "tool_result":
                        b.setdefault("state", {})["content"] = [{"type": "text", "text": p.text}]
                    else:              # thinking / text
                        b["text"] = p.text
                keep.append(b)
            if not keep:
                drop_rows.append(rid)
            elif changed:
                nd["content"] = keep
                updates[rid] = nd
        else:
            if its and its[0].deleted:
                drop_rows.append(rid)

    con = sqlite3.connect(sess.db, timeout=10)
    try:
        now = int(time.time() * 1000)
        for rid in drop_rows:
            con.execute("DELETE FROM session_message WHERE id = ?", (rid,))
        for rid, nd in updates.items():
            con.execute("UPDATE session_message SET data = ?, time_updated = ? WHERE id = ?",
                        (json.dumps(nd, ensure_ascii=False), now, rid))
        con.commit()
    finally:
        con.close()

    sess.load()
    sess.dirty = False
    return {"backup": bak, "records": len(sess.records),
            "geaendert": len(updates), "geloescht": len(drop_rows)}
