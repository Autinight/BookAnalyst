import argparse
from pathlib import Path
import uvicorn


def main():
    parser = argparse.ArgumentParser(
        description="BookAnalyst local mathematical book workbench"
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    from .app import create_app

    uvicorn.run(create_app(args.workspace), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
