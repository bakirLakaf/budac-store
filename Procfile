web: python startup.py && gunicorn "app:create_app()" --bind 0.0.0.0:$PORT --workers ${WEB_WORKERS:-1} --threads 4 --timeout 120 --max-requests 400 --max-requests-jitter 50
