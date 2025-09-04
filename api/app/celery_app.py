# Short version:

# Celery = the background job runner.

# Redis = the queue (broker) + results store that Celery uses.

# Here’s what they do in your project:

# What happens on /ingest

# You POST to /ingest (FastAPI).

# The API queues a job in Redis: ingest_dir_task("/data/company_kb").

# The Celery worker process (separate container) pulls the job from Redis and runs it:

# reads files → chunks → (dedupes) → embeds → upserts to Milvus.

# When done, the worker stores the result/status back in Redis.

# The API’s /ingest/status/{task_id} endpoint reads that status from Redis so you can poll progress.

# So: Celery does the heavy lifting outside your request/response path; Redis is the “mailroom” that moves jobs/results between API and worker.

# Why this matters

# No blocking: /ingest returns immediately; users aren’t stuck waiting minutes.

# Reliability: Celery can retry failed jobs, and you can scale workers independently.

# Throughput: multiple workers can process different documents in parallel.

# Observability: you can see job states (PENDING → STARTED → SUCCESS/FAILURE).

import os
from celery import Celery
# Broker (where jobs are queued), and
broker = os.getenv("REDIS_URL", "redis://redis:6379/0")
# Backend (where results are stored)

backend = os.getenv("REDIS_URL", "redis://redis:6379/0")
#Celery = Python task queue for background jobs (workers, retries, schedules).
celery = Celery("slm-chatbot", broker=broker, backend=backend)
celery.conf.update(
    task_acks_late=True,
    task_routes={"app.tasks.ingest_dir_task": {"queue": "ingest"}},
    worker_prefetch_multiplier=1,
)
