FROM ubuntu:24.04

LABEL org.mnemonic.fabric="smoke-gate" \
      description="Ephemeral fabric image for pre-merge validation"

RUN apt-get update && apt-get install -y \
    curl \
    git \
    ca-certificates \
    python3 \
    python3-pip \
    jq \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /fabric

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
