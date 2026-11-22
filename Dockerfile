# Reproducible Research Environment for Private Collaborative Computing: FHE vs. SMPC
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PCC_MPSPDZ_DIR=/opt/mp-spdz \
    PCC_CONFIG_DIR=/app/config \
    PCC_RESULTS_DIR=/app/results \
    PCC_NETWORK_INTERFACE=lo \
    PYTHONPATH=/app

# 1. Install system dependencies, Linux traffic control (iproute2/tc/netem),
#    MP-SPDZ runtime libraries, and TeXLive with IEEEtran support
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    iproute2 \
    iputils-ping \
    libboost-filesystem-dev \
    libboost-iostreams-dev \
    libboost-system-dev \
    libboost-thread-dev \
    libgmp-dev \
    libgomp1 \
    libntl-dev \
    libsodium-dev \
    libssl-dev \
    openssl \
    python3 \
    python3-pip \
    python3-venv \
    texlive-fonts-recommended \
    texlive-latex-base \
    texlive-latex-extra \
    texlive-publishers \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# 2. Install MP-SPDZ v0.4.4 precompiled virtual machines and provision 3-party SSL certificates
RUN mkdir -p /opt && \
    curl -fsSL https://github.com/data61/MP-SPDZ/releases/download/v0.4.4/mp-spdz-0.4.4.tar.xz -o /tmp/mp-spdz.tar.xz && \
    tar -xJf /tmp/mp-spdz.tar.xz -C /opt && \
    rm -f /tmp/mp-spdz.tar.xz && \
    ln -sfn /opt/mp-spdz-0.4.4 /opt/mp-spdz && \
    cd /opt/mp-spdz && \
    bash Scripts/tldr.sh && \
    bash Scripts/setup-ssl.sh 3 /opt/mp-spdz/Player-Data

WORKDIR /app

# 3. Install pinned Python dependencies including OpenFHE v1.6.1.0 (Ubuntu 24.04 wheel)
COPY requirements.txt /app/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /app/requirements.txt

# 4. Copy project modules
COPY . /app

# Ensure results subdirectories exist for volume mounts
RUN mkdir -p /app/results/raw /app/results/processed /app/results/graphs /app/results/reports

CMD ["python3", "-m", "orchestrator.runner", "--mode", "standard"]
