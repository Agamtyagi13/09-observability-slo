FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN useradd --system --uid 10001 app
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py ./
USER 10001
EXPOSE 8080
# One worker + threads: avoids forking after the OTel SDK background threads start.
CMD ["gunicorn", "-w", "1", "--threads", "8", "-b", "0.0.0.0:8080", "main:app"]
