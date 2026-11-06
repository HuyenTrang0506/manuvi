import argparse

import uvicorn

from .app import create_app


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m src.webapp")
    p.add_argument("--jobdir", required=True)
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    app = create_app(a.jobdir, port=a.port)
    print(f"Open: http://127.0.0.1:{a.port}/?t={app.state.token}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
