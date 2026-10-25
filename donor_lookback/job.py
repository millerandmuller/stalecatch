"""F4 Entry point for the nightly check. Container command for the Nebius Serverless Job; also usable locally."""
import json
import sys

from .config import load_dotenv
from .runner import run_check
from .services import build_services


def main() -> int:
    load_dotenv()
    svc = build_services()
    run = run_check(svc.store, svc.gateway, svc.fetcher, svc.settings, trigger="serverless-job")
    print(json.dumps(run["summary"], indent=2))
    return 0 if run["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
