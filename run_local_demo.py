"""Initialize the SQLite demo database and run one monitoring cycle."""

import os

from config import DEMO_DATABASE, get_settings
from src.database.models import Base
from src.database.session import get_session_factory


def main() -> int:
    os.environ["LOCAL_DEMO"] = "true"
    get_settings.cache_clear()
    get_session_factory.cache_clear()

    from src.tasks.monitoring import collect_monitoring

    sessions = get_session_factory()
    with sessions() as session:
        Base.metadata.create_all(session.get_bind())
    # collect_monitoring seeds the competitors in its first transaction.
    result = collect_monitoring(sessions, force_report=True)
    print(f"SQLite: {DEMO_DATABASE}")
    print(
        f"Competitors: {result['competitors']} (seeded: {result['seeded']}), "
        f"price snapshots: {result['price_snapshots']}, AI report: {result['insight_id']}"
    )
    for issue in result["issues"]:
        print(issue)
    return int(result["status"] != "completed" or result["insight_id"] is None)


if __name__ == "__main__":
    raise SystemExit(main())
