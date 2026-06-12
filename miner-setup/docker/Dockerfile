FROM python:3.11-slim

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get install -y \
        curl \
        bash \
        nodejs \
        npm \
        pkg-config \
        make \
        gcc \
        libpq-dev \
        git \
    && rm -rf /var/lib/apt/lists/*

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

COPY --from=ghcr.io/astral-sh/uv:0.10.12 /uv /uvx /bin/

WORKDIR /app

RUN git clone https://github.com/mode-network/synth-subnet.git . && \
    uv sync --frozen --no-dev

# Put the project venv first so python and btcli resolve to it
ENV PATH="/app/.venv/bin:${PATH}"
ENV PYTHONPATH="."

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

EXPOSE 8091

ENTRYPOINT ["./entrypoint.sh"]
