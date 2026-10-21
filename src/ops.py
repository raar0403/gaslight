"""Operationen auf einer Session. EINZIGER Schreibweg.

Alles Heikle steckt hier, nicht beim Agenten: der Agent nennt nur Indizes und
Text, die Konsistenz stellt dieses Modul her.

Garantien:
  * tool_use und tool_result werden immer gemeinsam entfernt
  * ein Record, dessen Bloecke alle weg sind, faellt raus
  * parentUuid wird beim Serialisieren neu verkettet (ueber Geloeschte hinweg)
  * bei tool_use wird input UND wireToolInputs[id] geschrieben -- Dubletten
  * geschrieben wird erst bei save(), vorher nur im Arbeitsmodell
"""
import json
from pathlib import Path


class OpError(Exception):
    pass


# ------------------------------------------------------------------ loeschen
def delete(sess, idxs, _reason=None):
    """Items loeschen. Gibt Bericht zurueck, was zusaetzlich mitging."""
    idxs = [int(i) for i in idxs]
    targets, added = set(), []
    for idx in idxs:
        it = sess.item(idx)
        if it is None:
            raise OpError("idx %d existiert nicht" % idx)
        if it.deleted:
            continue
        targets.add(it.idx)
        if it.kind in ("tool_use", "tool_result"):
            p = sess.pair_of(it)
            if p is None:
                raise OpError(
                    "idx %d (%s) hat kein Gegenstueck -- einseitig loeschen "
                    "wuerde die API-Anfrage zerstoeren" % (idx, it.kind))
            if p.idx not in targets:
                targets.add(p.idx)
                added.append({"idx": p.idx, "kind": p.kind, "grund": "Tool-Paar"})
    for i in targets:
        sess.items[i].deleted = True
    sess.dirty = True
    return {"geloescht": sorted(targets), "mitgezogen": added}


def restore(sess, idxs):
    out = []
    for idx in idxs:
        it = sess.item(int(idx))
        if it is None or not it.deleted:
            continue
        it.deleted = False
        out.append(it.idx)
        if it.kind in ("tool_use", "tool_result"):
            p = sess.pair_of(it)
            if p is not None and p.deleted:
                p.deleted = False
                out.append(p.idx)
    sess.dirty = True
    return sorted(set(out))


def purge_thinking(sess):
    """Alle Thinking-Bloecke weg. Laeuft beim Laden automatisch.

    Begruendung: der Klartext liegt zu ~95% gar nicht lokal vor (er steckt
    verschluesselt im signature-Blob), er ist ein grosser Token-Posten, und
    eine stehengebliebene Herleitung wuerde nach einem Edit zu einer Antwort
    gehoeren, die so nicht mehr dasteht.
    """
    hit = [i.idx for i in sess.items if i.kind == "thinking" and not i.deleted]
    saved = sum(sess.items[i].tokens for i in hit)
    for i in hit:
        sess.items[i].deleted = True
    sess.dirty = bool(hit)
    return {"anzahl": len(hit), "tokens": saved, "idxs": hit}


# ---------------------------------------------------------------- editieren
def rewrite(sess, idx, new_text):
    import tokens as _t
    it = sess.item(int(idx))
    if it is None:
        raise OpError("idx %s existiert nicht" % idx)
    if it.deleted:
        raise OpError("idx %s ist geloescht" % idx)
    if not it.editable:
        why = ("Thinking-Klartext liegt nicht lokal vor"
               if it.kind == "thinking" else "%s ist nicht editierbar" % it.kind)
        raise OpError("idx %s: %s -- nur loeschen moeglich" % (idx, why))
    if it.kind == "tool_use":
        try:
            json.loads(new_text)
        except json.JSONDecodeError as e:
            raise OpError("idx %s: Tool-Input muss gueltiges JSON sein (%s)" % (idx, e))
    if it.original is None:
        it.original = it.text
    it.text = new_text
    it.edited = (new_text != it.original)
    it.tokens = _t.count(new_text)
    sess.dirty = True
    return {"idx": it.idx, "tokens": it.tokens}


def revert(sess, idx):
    import tokens as _t
    it = sess.item(int(idx))
    if it is None or it.original is None:
        return False
    it.text = it.original
    it.tokens = _t.count(it.original)
    it.edited = False
    it.original = None
    sess.dirty = True
    return True


# --------------------------------------------------------------- schreiben
def _apply_to_records(sess):
    """Arbeitsmodell -> tiefe Kopie der Records, Edits eingesetzt."""
    recs = json.loads(json.dumps(sess.records, ensure_ascii=False))
    drop_blocks = {}
    for it in sess.items:
        if it.deleted:
            drop_blocks.setdefault(it.rec, []).append(it.bi)
            continue
        if not it.edited:
            continue
        rec = recs[it.rec]
        if it.bi == -1:
            rec["message"]["content"] = it.text
            continue
        b = rec["message"]["content"][it.bi]
        t = b.get("type")
        if t == "text":
            b["text"] = it.text
        elif t == "thinking":
            b["thinking"] = it.text
        elif t == "tool_use":
            b["input"] = json.loads(it.text)
            w = rec.get("wireToolInputs")
            if isinstance(w, dict) and b.get("id") in w:
                w[b["id"]] = b["input"]          # Dublette mitziehen
        elif t == "tool_result":
            if isinstance(b.get("content"), list):
                b["content"] = [{"type": "text", "text": it.text}]
            else:
                b["content"] = it.text

    drop_records = set()
    for ri, bis in drop_blocks.items():
        rec = recs[ri]
        if -1 in bis or rec.get("type") not in ("user", "assistant"):
            drop_records.add(ri)
            continue
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            drop_records.add(ri)
            continue
        keep = set(bis)
        rec["message"]["content"] = [b for i, b in enumerate(content) if i not in keep]
        if not rec["message"]["content"]:
            drop_records.add(ri)
    return recs, drop_records


def _relink(recs, drop):
    """parentUuid ueber geloeschte Records hinweg neu verketten."""
    parent = {r["uuid"]: r.get("parentUuid") for r in recs if "uuid" in r}
    gone = set()
    for i in drop:
        if "uuid" in recs[i]:
            gone.add(recs[i]["uuid"])
    out = []
    for ri, r in enumerate(recs):
        if ri in drop:
            continue
        if "uuid" in r:
            p = r.get("parentUuid")
            seen = set()
            while p is not None and p in gone and p not in seen:
                seen.add(p)
                p = parent.get(p)
            r["parentUuid"] = p
        out.append(r)
    return out


def validate(recs):
    """Invarianten pruefen, bevor irgendetwas die Platte beruehrt."""
    problems = []
    tu, tr = set(), set()
    uuids = set(r["uuid"] for r in recs if "uuid" in r)
    for r in recs:
        if r.get("type") == "assistant" and isinstance(r.get("message"), dict):
            for b in r["message"].get("content", []) or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    tu.add(b.get("id"))
        if r.get("type") == "user" and isinstance(r.get("message"), dict):
            c = r["message"].get("content")
            if isinstance(c, list):
                for b in c:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        tr.add(b.get("tool_use_id"))
        p = r.get("parentUuid")
        if p is not None and p not in uuids:
            problems.append("parentUuid zeigt ins Leere: %s" % str(r.get("uuid", "?"))[:8])
    for t in tu - tr:
        problems.append("tool_use %s ohne tool_result" % str(t)[:14])
    for t in tr - tu:
        problems.append("tool_result %s ohne tool_use" % str(t)[:14])
    for r in recs:
        if r.get("type") in ("user", "assistant"):
            c = r.get("message", {}).get("content")
            if isinstance(c, list) and not c:
                problems.append("leere Nachricht %s" % str(r.get("uuid", "?"))[:8])
    first = next((r for r in recs if "parentUuid" in r), None)
    if first is not None and first.get("parentUuid") is not None:
        problems.append("erster Record hat noch einen Vorgaenger")
    return problems


def preview(sess):
    if getattr(sess, "source", "claude") == "opencode":
        # opencode: Reihenfolge ueber seq, Tool-Paar ist ein Block -- es gibt
        # keine Ketten- oder Paarungsinvariante, die kaputtgehen koennte.
        return sess.records, []
    recs, drop = _apply_to_records(sess)
    recs = _relink(recs, drop)
    return recs, validate(recs)


def save(sess):
    if getattr(sess, "source", "claude") == "opencode":
        import opencode
        return opencode.save(sess)
    recs, problems = preview(sess)
    if problems:
        raise OpError("Nicht gespeichert, Struktur waere kaputt:\n  - " +
                      "\n  - ".join(problems[:10]))
    bak = sess.backup()
    tmp = Path(str(sess.path) + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        for r in recs:
            if "__unparsed__" in r:
                fh.write(r["__unparsed__"] + "\n")
            else:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(sess.path)
    sess.load()
    sess.dirty = False
    return {"backup": bak, "records": len(recs)}
