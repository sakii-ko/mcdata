FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV NVIDIA_DRIVER_CAPABILITIES=all

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    ffmpeg \
    git \
    libgl1 \
    libglvnd0 \
    libglx0 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    mesa-utils \
    openjdk-21-jre \
    python3 \
    python3-pip \
    python3-venv \
    tmux \
    x11-utils \
    x11-xserver-utils \
    xdotool \
    xserver-xorg-core \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/mcdata

CMD ["bash"]
