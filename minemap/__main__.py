"""Console entry point: start the local server and open the browser."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path


def _pick_dialog(kind: str, initial: str) -> int:
    """Hidden helper mode: show a native picker and print the chosen path. The
    server spawns this as a separate process so tkinter never runs on its threads."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "dir":
            path = filedialog.askdirectory(initialdir=initial or None)
        else:
            path = filedialog.askopenfilename(initialdir=initial or None)
    finally:
        root.destroy()
    sys.stdout.write(path or "")
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="minemap", description="MineMap local server")
    ap.add_argument("--pick", choices=["dir", "file"], help=argparse.SUPPRESS)
    ap.add_argument("--initial", default="", help=argparse.SUPPRESS)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    ap.add_argument("--project", default="", help="project.json (or its folder) to open")
    args = ap.parse_args(argv)
    if args.pick:
        return _pick_dialog(args.pick, args.initial)

    import uvicorn

    from .project import Project
    from .server import AppState, create_app

    project = None
    if args.project:
        p = Path(args.project)
        if p.is_dir():
            p = p / "project.json"
        if not p.exists():
            print(f"project not found: {p}", file=sys.stderr)
            return 2
        project = Project.load(p)

    state = AppState(project=project)
    if project is not None:
        state.set_project(project)
    app = create_app(state)
    url = f"http://{args.host}:{args.port}/"
    print(f"MineMap listening on {url}", flush=True)
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
