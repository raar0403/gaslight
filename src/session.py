"""Session-Modell.

Eine Session ist eine JSONL-Datei: eine Zeile je Record. Records haengen ueber
uuid/parentUuid als verkettete Liste zusammen -- auch die attachment-Zeilen,
nicht nur user/assistant.

Sichtbare Einheit fuer UI und Agent ist ein Item mit stabilem ganzzahligem idx.
Ein Item ist meist ein content-Block (text / thinking / tool_use / tool_result),
bei Nicht-Nachrichten-Records der Record selbst. UUIDs sieht niemand.

Geschrieben wird ausschliesslich ueber ops.py.
"""
import json
import shutil
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

import tokens

MSG_TYPES = ("user", "assistant")


@dataclass
class Item:
    idx: int
    rec: int                 # index in Session.records
    bi: int                  # index in message.content, -1 = content ist plain string
    role: str                # user | assistant | meta
    kind: str                # text | thinking | tool_use | tool_result | image | meta
    text: str = ""           # editierbarer Inhalt
    name: str = None         # Toolname
    tool_id: str = None
    meta_type: str = None    # bei kind=meta: attachment-typ o.ae.
    editable: bool = False
    tokens: int = 0
    deleted: bool = False
    edited: bool = False
    original: str = None
    has_signature: bool = False
    enc_bytes: int = 0       # Groesse des verschluesselten thinking-Blobs
    sub: str = None          # opencode: welcher Teil eines Blocks (input/output/reasoning/text)
    marker: str = None       # classifier_stop | refusal_fallback | <custom>
    flagged: bool = False    # diese Nachricht hat einen Klassifikator ausgeloest
    flag_category: str = None
    uuid: str = None


def _result_text(content):
    """tool_result.content ist str ODER Liste von Bloecken."""
    if isinstance(content, str):
        return content, True
    if isinstance(content, list):
        if len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
            return content[0].get("text", ""), True
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(b.get("text") or f"[{b.get('type')}]")
        return "\n".join(parts), False
    return json.dumps(content, ensure_ascii=False), False


class Session:
    def __init__(self, path):
        self.path = Path(path)
        self.source = "claude"
        self.records = []
        self.items = []
        self.bak = None
        self.dirty = False
        self.load()

    # ---------------------------------------------------------------- laden
    def load(self):
        self.records, self.items = [], []
        with open(self.path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.records.append(json.loads(line))
                except Exception:
                    self.records.append({"__unparsed__": line})
        self._build_items()

    def _build_items(self):
        self.items = []
        n = 0
        for ri, rec in enumerate(self.records):
            rtype = rec.get("type")
            ruuid = rec.get("uuid")
            if rtype in MSG_TYPES and isinstance(rec.get("message"), dict):
                content = rec["message"].get("content")
                if isinstance(content, str):
                    self.items.append(Item(n, ri, -1, rtype, "text", content,
                                           editable=True, tokens=tokens.count(content)))
                    n += 1
                    continue
                for bi, b in enumerate(content or []):
                    if not isinstance(b, dict):
                        continue
                    k = b.get("type")
                    it = Item(n, ri, bi, rtype, k)
                    if k == "text":
                        it.text = b.get("text", "")
                        it.editable = True
                    elif k == "thinking":
                        it.text = b.get("thinking", "") or ""
                        sig = b.get("signature") or ""
                        it.has_signature = bool(sig)
                        it.enc_bytes = (len(sig) * 3) // 4
                        it.editable = bool(it.text) and not sig
                    elif k == "tool_use":
                        it.name = b.get("name")
                        it.tool_id = b.get("id")
                        it.text = json.dumps(b.get("input", {}), ensure_ascii=False, indent=2)
                        it.editable = True
                    elif k == "tool_result":
                        it.tool_id = b.get("tool_use_id")
                        it.text, it.editable = _result_text(b.get("content"))
                        it.name = self._tool_name(b.get("tool_use_id"))
                    else:
                        it.text = f"[{k}]"
                    it.tokens = tokens.count(it.text) + (it.enc_bytes // 4 if it.kind == "thinking" else 0)
                    self.items.append(it)
                    n += 1
            else:
                mt = rtype
                if rtype == "attachment" and isinstance(rec.get("attachment"), dict):
                    mt = "attachment:" + str(rec["attachment"].get("type"))
                body = json.dumps(rec, ensure_ascii=False)
                self.items.append(Item(n, ri, -1, "meta", "meta", body[:400],
                                       meta_type=mt, tokens=tokens.count(body)))
                n += 1
        self._detect_markers()

    def _detect_markers(self):
        """Eingebaute Marker erkennen. Custom-Trigger kommen zusaetzlich aus
        der Config (prompts.py) und werden dort angehaengt."""
        for it in self.items:
            it.uuid = self.records[it.rec].get("uuid")
        # 1) Classifier-Stop: als User getarnte Systemmeldung
        for it in self.items:
            rec = self.records[it.rec]
            if (rec.get("type") == "user" and rec.get("turnCompanion")
                    and rec.get("isMeta") and "safety classifier" in (it.text or "")):
                it.marker = "classifier_stop"
        # 2) Refusal-Fallback und die geflaggte Nachricht
        by_uuid = {}
        for it in self.items:
            by_uuid.setdefault(it.uuid, it)
        for ri, rec in enumerate(self.records):
            if rec.get("subtype") == "model_refusal_fallback":
                cat = rec.get("apiRefusalCategory")
                for it in self.items:
                    if it.rec == ri:
                        it.marker = "refusal_fallback"
                        it.flag_category = cat
                refused = rec.get("refusedUserMessageUuid")
                tgt = by_uuid.get(refused)
                if tgt is not None:
                    tgt.flagged = True
                    tgt.flag_category = cat

    def _tool_name(self, tid):
        for rec in self.records:
            if rec.get("type") == "assistant" and isinstance(rec.get("message"), dict):
                for b in rec["message"].get("content", []) or []:
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") == tid:
                        return b.get("name")
        return None

    # ------------------------------------------------------------- zugriffe
    def item(self, idx):
        if 0 <= idx < len(self.items):
            return self.items[idx]
        return None

    def live(self):
        return [i for i in self.items if not i.deleted]

    def pair_of(self, it):
        """Partner-Item eines tool_use / tool_result."""
        if not it.tool_id:
            return None
        want = "tool_result" if it.kind == "tool_use" else "tool_use"
        for o in self.items:
            if o.kind == want and o.tool_id == it.tool_id:
                return o
        return None

    def backup(self):
        if self.bak:
            return self.bak
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.bak = str(self.path) + f".{stamp}.bak"
        shutil.copy2(self.path, self.bak)
        return self.bak

    def active_model(self):
        m = None
        for rec in self.records:
            if rec.get("type") == "assistant" and isinstance(rec.get("message"), dict):
                m = rec["message"].get("model") or m
        return m

    def flags(self):
        """Klassifikator-Ereignisse fuer den Zaehler."""
        from collections import Counter
        cats = Counter(i.flag_category for i in self.items
                       if i.marker == "refusal_fallback" and i.flag_category)
        return {
            "stops": sum(1 for i in self.items if i.marker == "classifier_stop"),
            "fallbacks": sum(1 for i in self.items if i.marker == "refusal_fallback"),
            "flagged_msgs": sum(1 for i in self.items if i.flagged),
            "kategorien": dict(cats),
        }

    def stats(self):
        live = self.live()
        return {
            "items": len(self.items),
            "live": len(live),
            "deleted": len(self.items) - len(live),
            "tokens_total": sum(i.tokens for i in self.items),
            "tokens_live": sum(i.tokens for i in live),
            "edited": sum(1 for i in self.items if i.edited),
            "active_model": self.active_model(),
            "flags": self.flags(),
        }

    def view(self, include_deleted=True):
        return [asdict(i) for i in self.items if include_deleted or not i.deleted]
