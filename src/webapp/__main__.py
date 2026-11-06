import argparse

import uvicorn

from .app import create_app


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m src.webapp")
    p.add_argument("--jobdir", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    a = p.parse_args()
    uvicorn.run(create_app(a.jobdir), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
