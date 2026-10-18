"""Startet claudeJaja und oeffnet den Browser.

    python start.py            (PowerShell, cmd, Git Bash)
    python start.py --port 9000 --no-browser
"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()

    import uvicorn
    from jaja.server import app

    url = "http://%s:%d/" % (a.host, a.port)
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    print("claudeJaja laeuft auf " + url)
    print("Beenden mit Strg+C")
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
