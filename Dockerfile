FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY core core
COPY app app
COPY solution.py .
RUN useradd --create-home appuser && mkdir /app/data /state && chown appuser:appuser /app /state
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
