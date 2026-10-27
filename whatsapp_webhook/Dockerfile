FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py logic.py funnel_report.py live_capture.py ./
ENV PORT=8080
CMD ["sh", "-c", "exec gunicorn --bind :$PORT --workers 2 --threads 8 --timeout 60 app:app"]
