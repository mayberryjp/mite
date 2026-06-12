FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /Mite

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create persistent volume directories
RUN mkdir -p /app/data /app/config /app/rules /app/analysis /app/logs

# Initialize database and directories on first run
RUN python -m src.main

EXPOSE 8080/tcp
EXPOSE 1514/udp
EXPOSE 1515/tcp

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
