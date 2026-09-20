import argparse
import logging
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="BookAnalyst local mathematical book workbench"
    )
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    from .service import WorkspaceService
    with WorkspaceService(args.workspace, args.port) as service:
        service.run()


if __name__ == "__main__":
    main()
