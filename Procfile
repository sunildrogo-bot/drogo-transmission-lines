web: python -m gunicorn --config gunicorn.conf.py app:app
worker: python job_worker.py --poll-seconds 2
release: flask --app app:app db upgrade
