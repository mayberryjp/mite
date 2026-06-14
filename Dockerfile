FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    supervisor \
    git \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /Mite

# Clone the repository
RUN git clone --depth 1 --branch main https://github.com/mayberryjp/mite.git .

RUN pip install --no-cache-dir -r requirements.txt

RUN cp supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Create persistent volume directories
RUN mkdir -p /app/data /app/logs

# Initialize database and directories on first run
RUN python -m src.main

EXPOSE 4060/tcp
EXPOSE 1514/udp
EXPOSE 1515/tcp

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
