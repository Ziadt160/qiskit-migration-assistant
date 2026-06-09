"""Start an RQ worker that consumes migration jobs.

    python -m src.worker.run

Uses SimpleWorker where `os.fork` is unavailable (Windows) — and in general for the
single-GPU case, since running jobs in-process keeps the embedding model resident
instead of reloading it in a forked work-horse per job.
"""

from __future__ import annotations

import os


def main() -> None:
    import redis
    from rq import Queue

    from src.config import get_settings

    connection = redis.from_url(get_settings().redis_url)
    queue = Queue("migrations", connection=connection)

    if hasattr(os, "fork"):
        from rq import Worker

        worker = Worker([queue], connection=connection)
    else:
        # Windows has no os.fork(); run jobs in the worker process itself.
        from rq import SimpleWorker

        worker = SimpleWorker([queue], connection=connection)

    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
