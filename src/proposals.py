"""Vorschlaege des Agenten. Nichts hier schreibt auf die Platte.

Der Agent feuert nicht, er schlaegt vor. Jeder Vorschlag nennt in based_on die
Indizes, auf die er sich stuetzt. Wird einer davon abgelehnt oder anders
entschieden als geplant, verlieren die abhaengigen Vorschlaege ihre Grundlage
und werden als 'hinfaellig' markiert -- statt dass sie stillschweigend auf
einem Stand aufbauen, den es nicht mehr gibt.
"""
from dataclasses import dataclass, field, asdict


OPEN, APPROVED, DECLINED, STALE = "offen", "angenommen", "abgelehnt", "hinfaellig"


@dataclass
class Proposal:
    pid: int
    kind: str                      # delete | rewrite
    reason: str = ""
    idxs: list = field(default_factory=list)      # bei delete
    idx: int = None                               # bei rewrite
    new_text: str = ""
    based_on: list = field(default_factory=list)
    status: str = OPEN
    note: str = ""                 # Rueckmeldung bei "nachbessern"
    round: int = 0

    def touches(self):
        if self.kind == "delete":
            return set(self.idxs)
        if self.kind == "switch_model":
            return set()          # rein beratend, veraendert nichts
        return {self.idx}


class Store:
    def __init__(self):
        self.items = []
        self._next = 1
        self.round = 0
        self.log = []

    def add(self, kind, **kw):
        p = Proposal(pid=self._next, kind=kind, round=self.round, **kw)
        self._next += 1
        self.items.append(p)
        return p

    def get(self, pid):
        return next((p for p in self.items if p.pid == pid), None)

    def open_items(self):
        return [p for p in self.items if p.status == OPEN]

    # ------------------------------------------------------- entscheidungen
    def decide(self, pid, status, note=""):
        p = self.get(pid)
        if p is None:
            return None, []
        p.status = status
        p.note = note
        stale = []
        if status in (DECLINED,) or note:
            stale = self._invalidate(p)
        return p, stale

    def _invalidate(self, p):
        """Alles, was sich auf die Indizes dieses Vorschlags stuetzte, faellt."""
        touched = p.touches()
        hit, changed = [p.pid], True
        while changed:
            changed = False
            for o in self.items:
                if o.status != OPEN or o.pid in hit:
                    continue
                if touched & set(o.based_on) or touched & o.touches():
                    o.status = STALE
                    hit.append(o.pid)
                    touched |= o.touches()
                    changed = True
        out = [x for x in hit if x != p.pid]
        if out:
            self.log.append("Vorschlag %d: %d abhaengige hinfaellig (%s)" %
                            (p.pid, len(out), ", ".join("#%d" % x for x in out)))
        return out

    def feedback_for_agent(self):
        """Was dem Agenten fuer die naechste Runde mitgegeben wird."""
        out = []
        for p in self.items:
            if p.status == DECLINED:
                out.append("Vorschlag %d (%s %s) ABGELEHNT.%s" % (
                    p.pid, p.kind, p.idxs or p.idx,
                    (" Begruendung: " + p.note) if p.note else ""))
            elif p.status == STALE:
                out.append("Vorschlag %d (%s %s) HINFAELLIG -- stuetzte sich auf %s, "
                           "was nicht so entschieden wurde. Neu pruefen." % (
                               p.pid, p.kind, p.idxs or p.idx,
                               ", ".join(str(b) for b in p.based_on)))
        return out

    def approved(self):
        return [p for p in self.items if p.status == APPROVED]

    def view(self):
        return [asdict(p) for p in self.items]

    def reset_open(self):
        """Vor einer neuen Agentenrunde: offene und hinfaellige verwerfen,
        Entscheidungen behalten."""
        self.items = [p for p in self.items
                      if p.status in (APPROVED, DECLINED)]
        self.round += 1
