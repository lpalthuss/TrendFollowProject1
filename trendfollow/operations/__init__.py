"""Stable operations API composing storage, queries, actions and job management."""

from datetime import datetime
from pathlib import Path
import threading
from .common import KST
from .accounts import WorkspaceStore
from .queries import OperationsQueries
from .intraday import IntradayService
from .actions import ActionRunner
from .jobs import JobManager


class Operations:
    def __init__(self, root, clock=None):
        self.root = Path(root).resolve()
        self.home = self.root / "outputs/ui"
        self.lock = threading.RLock()
        self.clock = clock or (lambda: datetime.now(KST))
        self.accounts = WorkspaceStore(self)
        self.queries = OperationsQueries(self)
        self.intraday_service = IntradayService(self)
        self.actions = ActionRunner(self)
        self.job_manager = JobManager(self)
        self.refresh_jobs()

    def now(self):
        return self.clock()

    def pulse(self, *args, **kwargs):
        return self.queries.pulse(*args, **kwargs)

    @property
    def registry(self):
        return self.accounts.registry

    @property
    def pool(self):
        return self.job_manager.pool

    @property
    def jobs(self):
        return self.job_manager.jobs

    @property
    def job_leases(self):
        return self.job_manager.job_leases

    @property
    def instance_id(self):
        return self.job_manager.instance_id

    @property
    def processes(self):
        return self.intraday_service.processes

    def path(self, *args, **kwargs):
        return self.accounts.path(*args, **kwargs)

    def registry_rows(self, *args, **kwargs):
        return self.accounts.registry_rows(*args, **kwargs)

    def workspaces(self, *args, **kwargs):
        return self.accounts.workspaces(*args, **kwargs)

    def workspace(self, *args, **kwargs):
        return self.accounts.workspace(*args, **kwargs)

    def configuration(self, *args, **kwargs):
        return self.accounts.configuration(*args, **kwargs)

    def presets(self, *args, **kwargs):
        return self.accounts.presets(*args, **kwargs)

    def create_workspace(self, *args, **kwargs):
        return self.accounts.create_workspace(*args, **kwargs)

    def state(self, *args, **kwargs):
        return self.accounts.state(*args, **kwargs)

    def latest_signals(self, *args, **kwargs):
        return self.queries.latest_signals(*args, **kwargs)

    def guidance(self, *args, **kwargs):
        return self.queries.guidance(*args, **kwargs)

    def revision_view(self, *args, **kwargs):
        return self.queries.revision_view(*args, **kwargs)

    def snapshot(self, *args, **kwargs):
        return self.queries.snapshot(*args, **kwargs)

    def completed_date(self, *args, **kwargs):
        return self.queries.completed_date(*args, **kwargs)

    def prices(self, *args, **kwargs):
        return self.queries.prices(*args, **kwargs)

    def plans(self, *args, **kwargs):
        return self.intraday_service.plans(*args, **kwargs)

    def run_path(self, *args, **kwargs):
        return self.intraday_service.run_path(*args, **kwargs)

    def start_watch(self, *args, **kwargs):
        return self.intraday_service.start_watch(*args, **kwargs)

    def intraday(self, *args, **kwargs):
        return self.intraday_service.intraday(*args, **kwargs)

    def stop_watch(self, *args, **kwargs):
        return self.intraday_service.stop_watch(*args, **kwargs)

    def refresh_jobs(self, *args, **kwargs):
        return self.job_manager.refresh_jobs(*args, **kwargs)

    def release_job(self, *args, **kwargs):
        return self.job_manager.release_job(*args, **kwargs)

    def close(self, *args, **kwargs):
        return self.job_manager.close(*args, **kwargs)

    def submit(self, *args, **kwargs):
        return self.job_manager.submit(*args, **kwargs)

    def execute(self, *args, **kwargs):
        return self.job_manager.execute(*args, **kwargs)

    def replay_revision(self, *args, **kwargs):
        return self.actions.replay_revision(*args, **kwargs)

    def revision_policy(self, *args, **kwargs):
        return self.actions.revision_policy(*args, **kwargs)

    def update_workspace(self, row):
        return self.accounts.update_workspace(row)
