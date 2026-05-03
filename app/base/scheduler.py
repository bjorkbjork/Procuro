from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler

from app.db.database import engine

scheduler = BlockingScheduler(
    jobstores={"default": SQLAlchemyJobStore(engine=engine)},
    job_defaults={"coalesce": True, "misfire_grace_time": 300},
)
