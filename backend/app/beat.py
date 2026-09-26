"""Health reflects the scheduler loop, not unchanged shelve-file timestamps."""
from pathlib import Path

from celery.beat import PersistentScheduler


class HeartbeatScheduler(PersistentScheduler):
    def tick(self, *args, **kwargs):
        delay = super().tick(*args, **kwargs)
        Path("/tmp/autofish-beat-heartbeat").touch()
        return delay
