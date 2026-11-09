FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core core
COPY app app
COPY solution.py .
COPY alembic.ini .
COPY migrations migrations
RUN useradd --create-home appuser && mkdir /app/data /state /knowledge && chown appuser:appuser /app /state /knowledge
USER appuser
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-127.0.0.1}\""]
