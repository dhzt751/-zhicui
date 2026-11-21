from concurrent.futures import ThreadPoolExecutor

from ..db import execute, now, update_job

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="job")


def create_job(kind: str, ref_type: str, ref_id: int) -> int:
    timestamp = now()
    return execute(
        "INSERT INTO jobs (kind, ref_type, ref_id, status, progress, stage, created_at, updated_at)"
        " VALUES (?, ?, ?, 'queued', 0, '排队中', ?, ?)",
        (kind, ref_type, ref_id, timestamp, timestamp),
    )


def progress(job_id: int, pct: int, stage: str) -> None:
    update_job(job_id, status="running", progress=max(0, min(99, int(pct))), stage=stage)


def finish(job_id: int, stage: str = "完成") -> None:
    update_job(job_id, status="done", progress=100, stage=stage)


def fail(job_id: int, message: str) -> None:
    update_job(job_id, status="failed", stage="失败", error=message[:800])


def submit(job_id: int, work, on_error=None) -> None:
    def runner() -> None:
        update_job(job_id, status="running", progress=2, stage="开始处理")
        try:
            work(lambda pct, stage: progress(job_id, pct, stage))
            finish(job_id)
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or exc.__class__.__name__
            fail(job_id, message)
            if on_error:
                try:
                    on_error(message)
                except Exception:
                    pass

    _executor.submit(runner)