"""Findet Sessions und baut den Laufwerksbaum.

Der Ordnername (F--Pythoncode-claudeJaja) ist verlustbehaftet: ein '-' im
echten Pfad wird wie ein Trenner escaped. Deshalb wird der Pfad NICHT aus dem
Ordnernamen zurueckgerechnet, sondern aus dem Feld 'cwd' der ersten Zeile der
Session gelesen.
"""
import json
import os
import pathlib
from pathlib import Path

PROJECTS = Path.home() / ".claude" / "projects"


def _first_json_lines(path: Path, limit: int = 40):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return out


def _title(path: Path, head, tail_titles):
    side = path.with_suffix("")
    ct = side / "custom-title.json"
    if ct.exists():
        try:
            t = json.loads(ct.read_text(encoding="utf-8")).get("customTitle")
            if t:
                return t
        except Exception:
            pass
    if tail_titles:
        return tail_titles[-1]
    for d in head:
        if d.get("type") == "user" and isinstance(d.get("message"), dict):
            c = d["message"].get("content")
            if isinstance(c, list):
                c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
            if isinstance(c, str) and c.strip():
                return c.strip().split("\n")[0][:70]
    return path.stem[:8]


def _scan_titles(path: Path):
    """Letzten custom-title / ai-title aus der Datei ziehen (guenstig: nur diese Zeilen)."""
    found = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"custom-title"' not in line and '"ai-title"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                t = d.get("customTitle") or d.get("aiTitle")
                if t:
                    found.append(t)
    except OSError:
        pass
    return found


_CACHE = {"stand": None, "daten": None}


def sessions(force=False):
    """Alle Sessions als flache Liste mit echtem cwd.

    Das Einlesen der Titel kostet bei tausenden Sessions spuerbar Zeit, darum
    gecacht bis sich ein Projektordner aendert.
    """
    try:
        stand = max((p.stat().st_mtime for p in PROJECTS.iterdir() if p.is_dir()),
                    default=0)
    except OSError:
        stand = 0
    if not force and _CACHE["stand"] == stand and _CACHE["daten"] is not None:
        return _CACHE["daten"]
    out = []
    if not PROJECTS.exists():
        return out
    for proj in sorted(PROJECTS.iterdir()):
        if not proj.is_dir():
            continue
        for f in proj.glob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            if st.st_size == 0:
                continue
            head = _first_json_lines(f)
            cwd = next((d["cwd"] for d in head if d.get("cwd")), None)
            titles = _scan_titles(f)
            out.append({
                "id": f.stem,
                "file": str(f),
                "cwd": cwd or proj.name,
                "title": _title(f, head, titles),
                "size": st.st_size,
                "mtime": st.st_mtime,
            })
    out.sort(key=lambda s: s["mtime"], reverse=True)
    _CACHE["stand"], _CACHE["daten"] = stand, out
    return out


def tree(source="claude"):
    """Baum von Laufwerk/Wurzel bis Projektordner, Sessions an den Blaettern.

    Knoten: {"name":..., "path":..., "children":[...], "sessions":[...]}
    source: "claude" (jsonl-Dateien) oder "opencode" (SQLite-DB).
    """
    if source == "opencode":
        import opencode
        src = opencode.sessions()
    else:
        src = sessions()
    roots = {}

    def child(node, name, path):
        for c in node["children"]:
            if c["name"] == name:
                return c
        c = {"name": name, "path": path, "children": [], "sessions": []}
        node["children"].append(c)
        return c

    for s in src:
        pp = pathlib.PurePath(s["cwd"])
        parts = list(pp.parts)
        if not parts:
            continue
        head = parts[0].replace("\\", "") or "/"
        if len(head) == 2 and head[1] == ":":
            head = head.upper()   # F: und f: sind dasselbe Laufwerk
            parts[0] = head + "\\"
        node = roots.get(head)
        if node is None:
            node = roots[head] = {"name": head, "path": parts[0],
                                  "children": [], "sessions": []}
        acc = parts[0]
        for part in parts[1:]:
            acc = str(pathlib.PurePath(acc) / part)
            node = child(node, part, acc)
        node["sessions"].append(s)

    def sort(n):
        n["children"].sort(key=lambda c: c["name"].lower())
        n["sessions"].sort(key=lambda x: x["mtime"], reverse=True)
        n["count"] = len(n["sessions"]) + sum(sort(c) for c in n["children"])
        return n["count"]

    out = [roots[k] for k in sorted(roots)]
    for r in out:
        sort(r)
    return out
