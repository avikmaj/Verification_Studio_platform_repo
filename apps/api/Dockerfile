# UVM Verification Studio API — Railway backend.
#
# Ships the full execution stack: Verilator (built from source, because the
# distro package lags the class/UVM improvements), Z3 for randomize(), GTKWave
# for FST reading, and the Accellera UVM library.
#
# The Verilator build is the expensive layer and is deliberately first so it
# caches across application changes.
FROM ubuntu:24.04 AS verilator-build

ARG VERILATOR_TAG=v5.050
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        git perl python3 make autoconf g++ flex bison ccache \
        libgoogle-perftools-dev numactl perl-doc libfl2 libfl-dev \
        zlib1g-dev liblz4-dev libzstd-dev help2man ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch ${VERILATOR_TAG} \
        https://github.com/verilator/verilator.git /src/verilator \
    && cd /src/verilator \
    && autoconf && ./configure --prefix=/opt/verilator \
    && make -j"$(nproc)" && make install \
    && rm -rf /src/verilator

# --------------------------------------------------------------------------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/verilator/bin:$PATH \
    UVM_HOME=/opt/uvm-core/src \
    UVMSTUDIO_WORKSPACE=/workspace

RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git make g++ perl \
        zlib1g liblz4-1 libzstd1 libgoogle-perftools4t64 \
        gtkwave ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verilator-build /opt/verilator /opt/verilator

# Accellera UVM — fetched, never vendored into this repository.
RUN git clone --depth 1 https://github.com/accellera-official/uvm-core.git /opt/uvm-core \
    && rm -rf /opt/uvm-core/.git

WORKDIR /app
COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/apps/api/requirements.txt

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY apps /app/apps
COPY examples /app/examples
RUN pip3 install --no-cache-dir --break-system-packages -e /app

# Seed the workspace with the golden acceptance project so a fresh deployment
# has something real to compile and run.
RUN mkdir -p /workspace && cp -r /app/examples/golden_apb /workspace/golden_apb

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,os,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn apps.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
