"""Proactive scheduler — runs agent tasks on a schedule without you asking.

An asyncio background loop wakes every TICK seconds, finds due scheduled tasks,
runs each through the agent loop autonomously, and files the result as a
notification. This is the "ambient" layer: morning briefings, periodic checks,
watch-and-report. It runs for as long as the server process is alive (on the VM,
that's always).

Schedules:
  - interval: run every N seconds
  - daily:    run once per day at a local HH:MM
"""

import asyncio
from datetime import datetime, timedelta

from .agent import run_once
from .storage import Storage

TICK_SECONDS = 30
# Guard against pathologically frequent interval tasks.
MIN_INTERVAL_SEC = 60
# A conversation id prefix so proactive runs get their own persistent thread per task.
_CONV_TITLE = "⏰ {title}"


def compute_next_run(
    schedule_kind: str,
    now: float,
    interval_sec: int | None = None,
    time_of_day: str | None = None,
) -> float:
    """Return the next epoch timestamp this schedule should fire."""
    if schedule_kind == "interval":
        step = max(int(interval_sec or MIN_INTERVAL_SEC), MIN_INTERVAL_SEC)
        return now + step
    if schedule_kind == "daily":
        hh, mm = _parse_hhmm(time_of_day)
        now_dt = datetime.fromtimestamp(now)
        target = now_dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now_dt:
            target = target + timedelta(days=1)
        return target.timestamp()
    raise ValueError(f"Unknown schedule_kind: {schedule_kind}")


def initial_next_run(
    schedule_kind: str,
    now: float,
    interval_sec: int | None = None,
    time_of_day: str | None = None,
) -> float:
    """First fire time when a task is created.

    Interval tasks fire one interval from now; daily tasks fire at the next HH:MM.
    """
    return compute_next_run(schedule_kind, now, interval_sec, time_of_day)


def _parse_hhmm(text: str | None) -> tuple[int, int]:
    try:
        hh, mm = (text or "09:00").split(":")
        return max(0, min(23, int(hh))), max(0, min(59, int(mm)))
    except Exception:
        return 9, 0


class Scheduler:
    def __init__(self, store: Storage):
        self._store = store
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._run_due()
            except Exception:
                # Never let one bad tick kill the scheduler.
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_SECONDS)
            except asyncio.TimeoutError:
                pass

    async def _run_due(self) -> None:
        import time

        now = time.time()
        for task in self._store.get_due_tasks(now):
            await self.run_task(task, scheduled=True)

    async def run_task(self, task: dict, scheduled: bool = False) -> dict:
        """Execute one scheduled task now, record a notification, advance its schedule."""
        import time

        conv_id = self._ensure_conversation(task)
        started = time.time()
        try:
            answer = await run_once(self._store, conv_id, task["prompt"])
            self._store.add_notification(
                title=task["title"],
                content=answer or "(no output)",
                task_id=task["id"],
                status="ok",
            )
            result = {"status": "ok", "content": answer}
        except Exception as e:  # noqa: BLE001
            self._store.add_notification(
                title=task["title"],
                content=f"Task failed: {type(e).__name__}: {e}",
                task_id=task["id"],
                status="error",
            )
            result = {"status": "error", "content": str(e)}

        if scheduled:
            nxt = compute_next_run(
                task["schedule_kind"], started,
                task.get("interval_sec"), task.get("time_of_day"),
            )
            self._store.update_task_schedule(task["id"], started, nxt)
        return result

    def _ensure_conversation(self, task: dict) -> str:
        """Give each scheduled task a stable conversation so its runs share context."""
        conv_id = f"task-{task['id']}"
        if not self._store.conversation_exists(conv_id):
            # create_conversation generates its own id, so insert directly with our id.
            import time
            with self._store._lock:  # reuse the store's lock for a direct insert
                self._store._conn.execute(
                    "INSERT OR IGNORE INTO conversations (id, title, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?)",
                    (conv_id, _CONV_TITLE.format(title=task["title"]), time.time(), time.time()),
                )
                self._store._conn.commit()
        return conv_id
