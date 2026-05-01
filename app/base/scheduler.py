from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler

from app.db.database import engine

scheduler = BackgroundScheduler(
    jobstores={"default": SQLAlchemyJobStore(engine=engine)},
    job_defaults={"coalesce": True, "misfire_grace_time": 300},
)
