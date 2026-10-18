"""Lokaler Agent ueber Ollama.

Der Agent bekommt die Historie nie am Stueck -- dafuer ist sie zu gross und
ein lokales Modell zu klein. Er arbeitet ueber Werkzeuge: auflisten, greppen,
einzelne Nachrichten lesen. Schreiben kann er nicht. Er kann ausschliesslich
Vorschlaege erzeugen; das Anwenden macht jaja.ops, nachdem du zugestimmt hast.
"""
import json
import re

import requests

from . import prompts

OLLAMA = "http://127.0.0.1:11434"

def tool_defs():
    def fn(name, desc, props, req):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}

    return [
        fn("list_messages",
           "Uebersicht ueber den Verlauf: idx, Rolle, Art, geschaetzte Tokens und "
           "der Anfang des Textes. Ohne Argumente die ganze Uebersicht.",
           {"start": {"type": "integer", "description": "ab welchem idx"},
            "count": {"type": "integer", "description": "wie viele, Standard 60"}},
           []),
        fn("grep",
           "Sucht einen regulaeren Ausdruck im gesamten Verlauf und liefert die "
           "Treffer mit idx und Fundstelle. Der schnellste Weg zu relevanten Stellen.",
           {"muster": {"type": "string", "description": "Suchmuster, Regex"}},
           ["muster"]),
        fn("read",
           "Liest eine Nachricht vollstaendig.",
           {"idx": {"type": "integer"},
            "max_zeichen": {"type": "integer", "description": "Standard 4000"}},
           ["idx"]),
        fn("propose_delete",
           "Schlaegt vor, Nachrichten zu loeschen. Tool-Paare zieht das Programm "
           "selbst mit, das musst du nicht beachten.",
           {"idxs": {"type": "array", "items": {"type": "integer"}},
            "grund": {"type": "string"},
            "based_on": {"type": "array", "items": {"type": "integer"},
                         "description": "Indizes, auf die du dich stuetzt"}},
           ["idxs", "grund"]),
        fn("propose_rewrite",
           "Schlaegt vor, den Text einer Nachricht zu ersetzen. Vollstaendiger "
           "neuer Text, nicht nur die Aenderung.",
           {"idx": {"type": "integer"},
            "neuer_text": {"type": "string"},
            "grund": {"type": "string"},
            "based_on": {"type": "array", "items": {"type": "integer"}}},
           ["idx", "neuer_text", "grund"]),
        fn("propose_model_switch",
           "Empfiehlt, eine Stelle des Verlaufs auf einem anderen Modell laufen "
           "zu lassen (z.B. auf das Fallback-Modell, wenn ein Klassifikator wiederholt "
           "ausloest). Reine Empfehlung -- wird nicht ausgefuehrt.",
           {"idx": {"type": "integer", "description": "betroffene Stelle"},
            "ziel": {"type": "string", "description": "empfohlenes Modell"},
            "grund": {"type": "string"}},
           ["ziel", "grund"]),
        fn("finish", "Aufrufen, wenn alle Vorschlaege gemacht sind.",
           {"zusammenfassung": {"type": "string"}}, ["zusammenfassung"]),
    ]


class Runner:
    """Fuehrt eine Agentenrunde aus. Werkzeuge greifen nur lesend auf die
    Session zu; Schreibzugriff hat der Agent nicht."""

    def __init__(self, sess, store, model, on_event=None):
        self.s = sess
        self.store = store
        self.model = model
        self.on_event = on_event or (lambda e: None)
        self.done = False
        self.summary = ""

    # ----------------------------------------------------------- werkzeuge
    def t_list_messages(self, start=0, count=60):
        rows = [i for i in self.s.live() if i.role != "meta"]
        sel = [i for i in rows if i.idx >= int(start)][:int(count or 60)]
        out = ["idx | rolle     | art         | ~tok | marker/flag | anfang"]
        for i in sel:
            head = " ".join(i.text.split())[:64]
            label = i.name or i.kind
            mk = i.marker or ("GEFLAGGT:" + str(i.flag_category) if i.flagged else "")
            out.append("%3d | %-9s | %-11s | %4d | %-11s | %s" %
                       (i.idx, i.role, label[:11], i.tokens, mk[:11], head))
        out.append("(%d von %d Nachrichten)" % (len(sel), len(rows)))
        return "\n".join(out)

    def t_grep(self, muster):
        try:
            rx = re.compile(muster, re.I)
        except re.error as e:
            return "ungueltiges Muster: %s" % e
        out = []
        for i in self.s.live():
            if i.role == "meta":
                continue
            for m in rx.finditer(i.text or ""):
                a, b = max(0, m.start() - 60), m.end() + 60
                out.append("idx %d (%s, ~%d tok): ...%s..." %
                           (i.idx, i.name or i.kind, i.tokens,
                            " ".join(i.text[a:b].split())))
                break
            if len(out) >= 40:
                out.append("... weitere Treffer abgeschnitten")
                break
        return "\n".join(out) if out else "keine Treffer"

    def t_read(self, idx, max_zeichen=4000):
        it = self.s.item(int(idx))
        if it is None:
            return "idx %s gibt es nicht" % idx
        if it.deleted:
            return "idx %s ist bereits geloescht" % idx
        txt = it.text or ""
        cut = ""
        if len(txt) > int(max_zeichen or 4000):
            cut = "\n... [%d Zeichen abgeschnitten]" % (len(txt) - int(max_zeichen))
            txt = txt[:int(max_zeichen)]
        return "idx %d | %s | %s | ~%d tok | editierbar: %s\n---\n%s%s" % (
            it.idx, it.role, it.name or it.kind, it.tokens,
            "ja" if it.editable else "nein", txt, cut)

    def t_propose_delete(self, idxs, grund="", based_on=None):
        idxs = [int(x) for x in (idxs or [])]
        bad = [x for x in idxs if self.s.item(x) is None]
        if bad:
            return "diese Indizes gibt es nicht: %s" % bad
        p = self.store.add("delete", idxs=idxs, reason=grund,
                           based_on=[int(x) for x in (based_on or [])])
        self.on_event({"typ": "vorschlag", "pid": p.pid})
        return "Vorschlag %d angelegt (loeschen %s)." % (p.pid, idxs)

    def t_propose_rewrite(self, idx, neuer_text, grund="", based_on=None):
        it = self.s.item(int(idx))
        if it is None:
            return "idx %s gibt es nicht" % idx
        if not it.editable:
            return ("idx %s ist nicht editierbar (%s) -- hier geht nur loeschen"
                    % (idx, it.kind))
        p = self.store.add("rewrite", idx=int(idx), new_text=neuer_text,
                           reason=grund, based_on=[int(x) for x in (based_on or [])])
        self.on_event({"typ": "vorschlag", "pid": p.pid})
        return "Vorschlag %d angelegt (umschreiben idx %s)." % (p.pid, idx)

    def t_propose_model_switch(self, ziel, grund="", idx=None):
        p = self.store.add("switch_model", idx=(int(idx) if idx is not None else None),
                           new_text=ziel, reason=grund)
        self.on_event({"typ": "vorschlag", "pid": p.pid})
        return "Vorschlag %d angelegt (Modellwechsel-Empfehlung: %s)." % (p.pid, ziel)

    def t_finish(self, zusammenfassung=""):
        self.done = True
        self.summary = zusammenfassung
        return "ok"

    # --------------------------------------------------------------- lauf
    def _call(self, messages):
        cfg = prompts.load()
        r = requests.post(OLLAMA + "/api/chat", timeout=600, json={
            "model": self.model, "messages": messages,
            "tools": tool_defs(), "stream": False,
            "think": bool(cfg.get("think", False)),
            "keep_alive": cfg.get("keep_alive", "30m"),
            "options": {"num_ctx": int(cfg.get("num_ctx", 16384))}})
        r.raise_for_status()
        return r.json()["message"]

    def run(self, auftrag, max_runden=24):
        cfg = prompts.load()
        prompts.apply_custom_triggers(self.s, cfg)
        st = self.s.stats()
        lage = ("Session: %d Nachrichten sichtbar, ~%d Tokens gesamt.\n"
                "Auftrag des Nutzers:\n%s" % (st["live"], st["tokens_live"], auftrag))
        fb = self.store.feedback_for_agent()
        if fb:
            lage += ("\n\nAus der letzten Runde:\n- " + "\n- ".join(fb) +
                     "\nBeruecksichtige das.")
        msgs = [{"role": "system", "content": prompts.build_system(self.s, cfg)},
                {"role": "user", "content": lage}]

        for runde in range(max_runden):
            try:
                m = self._call(msgs)
            except Exception as e:
                self.on_event({"typ": "fehler", "text": str(e)})
                return {"ok": False, "fehler": str(e)}
            msgs.append(m)
            calls = m.get("tool_calls") or []
            if m.get("content"):
                self.on_event({"typ": "text", "text": m["content"]})
            if not calls:
                break
            for c in calls:
                f = c.get("function", {})
                name = f.get("name")
                args = f.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                fn = getattr(self, "t_" + name, None)
                self.on_event({"typ": "werkzeug", "name": name, "args": args})
                if fn is None:
                    res = "unbekanntes Werkzeug: %s" % name
                else:
                    try:
                        res = fn(**args)
                    except TypeError as e:
                        res = "falsche Argumente: %s" % e
                    except Exception as e:
                        res = "Fehler: %s" % e
                msgs.append({"role": "tool", "content": str(res)})
            if self.done:
                break
        return {"ok": True, "zusammenfassung": self.summary,
                "vorschlaege": len(self.store.open_items()), "runden": runde + 1}


def models():
    try:
        r = requests.get(OLLAMA + "/api/tags", timeout=4)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return []
