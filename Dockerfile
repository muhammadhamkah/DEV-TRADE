FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends iptables dnsutils \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin agent

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY survival ./survival
COPY prompts ./prompts
COPY scripts/entrypoint.sh scripts/lockdown.sh ./scripts/
RUN chmod +x scripts/*.sh && mkdir -p /app/state && chown agent:agent /app/state

ENV SURVIVAL_STATE_DIR=/app/state
ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["run"]
