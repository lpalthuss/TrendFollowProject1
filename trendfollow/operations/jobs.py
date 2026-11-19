"""Jobs responsibilities for the local operations UI."""

from concurrent.futures import ThreadPoolExecutor
import os
import traceback
import uuid
from ..ui_jobs import acquire_lease, load_jobs, save_job


class JobManager:
    def __init__(self, context):
        self.context = context
        self.pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="trendfollow-ui"
        )
        self.jobs = {}
        self.instance_id = uuid.uuid4().hex
        self.job_leases = {}

    def refresh_jobs(self):
        with self.context.lock:
            for job in load_jobs(self.context.home, self.context.instance_id):
                local = self.context.jobs.get(job["id"])
                if local and job["id"] in self.context.job_leases:
                    continue
                self.context.jobs[job["id"]] = job

    def release_job(self, key):
        with self.context.lock:
            leases = self.context.job_leases.pop(key, ())
        for lease in leases:
            lease.release()

    def report_progress(self, key, message, current=None, total=None,
                        stage_current=None, stage_total=None):
        with self.context.lock:
            job = dict(self.jobs[key], progress=message)
            job["progress_current"] = current
            job["progress_total"] = total
            job["stage_current"] = stage_current
            job["stage_total"] = stage_total
            save_job(self.context.home / "jobs" / f"{key}.json", job)
            self.jobs[key] = job

    def close(self):
        # Explicitly started quote workers have their own bounded lifetime and OFF control.
        self.context.pool.shutdown(wait=False, cancel_futures=True)

    def submit(self, payload):
        w = self.context.workspace(payload["workspace"])
        action = payload.get("action")
        if action not in {
            "analyze",
            "market_analyze",
            "paper",
            "daily",
            "industry",
            "candidate_universe",
            "candidate_policy",
            "prepare_intraday",
            "watch_auto",
            "intraday_close",
            "volume_import",
            "volume_profile",
            "revision_check",
            "revision_continue",
            "revision_replay",
        }:
            raise ValueError("지원하지 않는 작업입니다.")
        if action == "candidate_policy":
            state = self.context.state(w)
            if not state:
                raise ValueError("먼저 계좌의 첫 종가 처리를 완료하세요.")
            as_of = state["last_date"]
        elif action == "candidate_universe":
            from ..candidate_universe import latest_completed_session

            if payload.get("candidate_source") not in {"kis", "krx_open_api", "pykrx"}:
                raise ValueError("후보군 자료 원천이 전달되지 않았습니다. UI 서버를 재시작하고 화면을 새로고침하세요.")
            as_of = latest_completed_session(self.context.now())
        elif action in {"prepare_intraday", "watch_auto"}:
            from ..krx_sessions import require_previous_close

            as_of = require_previous_close(payload.get("session"), payload.get("as_of"))
        else:
            as_of = payload.get("as_of")
        as_of = self.context.completed_date(as_of)
        if w.get("mode") == "recalculation" and action not in {
            "analyze",
            "market_analyze",
            "revision_check",
            "candidate_universe",
        }:
            raise ValueError(
                "정정 재계산 계좌는 고정 자료를 사용하는 비교 결과입니다. 원계좌에서 운용을 계속하세요."
            )
        if action == "revision_continue" and payload.get("reviewed") is not True:
            raise ValueError("변경 내역과 이전 결정 보존 방식을 확인하세요.")
        if (
            self.context.state(w)
            and action in {"analyze", "paper", "daily", "prepare_intraday", "watch_auto"}
            and self.context.snapshot(w["id"])["status"] == "configuration_mismatch"
        ):
            raise ValueError(
                "계좌 설정·유니버스가 변경되었습니다. 기존 계좌를 보존하고 새 비교 계좌를 사용하세요."
            )
        with self.context.lock:
            self.context.refresh_jobs()
            if any(
                j["status"] in {"queued", "running"} for j in self.context.jobs.values()
            ):
                raise ValueError("다른 작업이 진행 중입니다. 완료 후 실행하세요.")
            if any(j["status"] == "unconfirmed" for j in self.context.jobs.values()):
                raise ValueError(
                    "실행 여부가 미확인인 이전 작업이 있습니다. 다른 서버와 계좌 결과를 먼저 확인하세요."
                )
            shared = acquire_lease(self.context.home / "jobs" / ".executor.lock")
            if shared is None:
                raise ValueError(
                    "다른 UI 서버에서 작업이 진행 중입니다. 완료 후 실행하세요."
                )
            key = uuid.uuid4().hex[:16]
            lease = acquire_lease(self.context.home / "jobs" / f"{key}.lock")
            if lease is None:
                shared.release()
                raise ValueError("작업 실행권을 확보하지 못했습니다.")
            self.context.job_leases[key] = (lease, shared)
            job = dict(
                id=key,
                workspace=w["id"],
                action=action,
                as_of=as_of,
                status="queued",
                progress="실행 대기 중",
                created_at=self.context.now().isoformat(),
                owner_id=self.context.instance_id,
                owner_pid=os.getpid(),
            )
            if action == "candidate_universe":
                job["candidate_source"] = payload["candidate_source"]
                if job["candidate_source"] in {"krx_open_api", "pykrx"}:
                    job["from_as_of"] = payload.get("from_as_of")
            self.context.jobs[key] = job
            try:
                save_job(self.context.home / "jobs" / f"{key}.json", job)
                future = self.context.pool.submit(
                    self.context.execute, key, w, dict(payload)
                )
                future.add_done_callback(lambda _future: self.context.release_job(key))
            except Exception:
                self.context.release_job(key)
                self.context.jobs.pop(key, None)
                raise
        return dict(job)

    def execute(self, key, w, payload):
        job = dict(self.context.jobs[key])
        job["status"] = "running"
        job["progress"] = "작업 시작 중"
        save_job(self.context.home / "jobs" / f"{key}.json", job)
        with self.context.lock:
            self.context.jobs[key] = dict(job)
        try:
            result = self.context.actions.run(key, w, payload, job["as_of"])
            job.update(status="completed", result=result)
        except (ValueError, FileNotFoundError) as error:
            job.update(status="failed", error=str(error))
            if hasattr(error, "report"):
                job["revision_id"] = error.report["id"]
        except Exception as error:
            diagnostic = self.context.home / "jobs" / f"{key}.traceback.txt"
            try:
                diagnostic.write_text("".join(traceback.format_exception(error)), encoding="utf-8")
            except OSError:
                pass
            if isinstance(error, RuntimeError) and "cannot schedule new futures after interpreter shutdown" in str(error):
                job.update(status="interrupted", error="UI 서버 종료로 작업이 중단됐습니다. "
                           "완료된 거래일은 계좌에 보존됐으며, 서버 재시작 후 남은 날짜를 이어 처리할 수 있습니다.")
            else:
                job.update(
                    status="failed",
                    error=f"예상하지 못한 {type(error).__name__} 오류입니다. 작업 ID {key}의 서버 진단 기록을 확인하세요.",
                )
        finally:
            job["finished_at"] = self.context.now().isoformat()
            with self.context.lock:
                job["progress"] = self.context.jobs[key].get("progress", job["progress"])
                job["progress_current"] = self.context.jobs[key].get("progress_current")
                job["progress_total"] = self.context.jobs[key].get("progress_total")
            try:
                save_job(self.context.home / "jobs" / f"{key}.json", job)
            finally:
                with self.context.lock:
                    self.context.release_job(key)
                    self.context.jobs[key] = job
