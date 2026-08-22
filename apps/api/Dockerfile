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
    UVMSTUDIO_WORKSPACE=/workspace \
    CCACHE_DIR=/tmp/ccache

# The runtime image is also a BUILD image: Verilator does not ship a
# prebuilt runtime library. Every `verilator --binary` invocation compiles
# verilated*.cpp (including FST tracing, which #includes lz4.h and zstd.h)
# inside the user's build directory. So this stage needs compilers, make,
# perl, ccache (baked into verilated.mk at configure time) AND the -dev
# headers — not just the shared libraries.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip python3-venv git make g++ perl \
        ccache \
        zlib1g-dev liblz4-dev libzstd-dev libgoogle-perftools-dev \
        gtkwave ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=verilator-build /opt/verilator /opt/verilator

# Accellera UVM — fetched, never vendored into this repository.
# Pinned. An unpinned clone means a rebuild six months from now silently gets a
# different UVM and every recorded repro.json becomes a lie about what ran.
ARG UVM_TAG=2020.3.1
RUN git clone --depth 1 --branch "${UVM_TAG}" \
        https://github.com/accellera-official/uvm-core.git /opt/uvm-core \
    && rm -rf /opt/uvm-core/.git \
    && test -f /opt/uvm-core/src/uvm_pkg.sv

WORKDIR /app
COPY apps/api/requirements.txt /app/apps/api/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/apps/api/requirements.txt

COPY pyproject.toml README.md /app/
COPY src /app/src
COPY apps /app/apps
COPY examples /app/examples
RUN pip3 install --no-cache-dir --break-system-packages -e /app

# Seed the workspace with the golden acceptance project AND the eCPRI VIP so
# remote runs exercise more than one project (red-team bound 5: "the remote
# 4/4 covers the golden project only" — retired by seeding oran_ecpri).
RUN mkdir -p /workspace \
    && cp -r /app/examples/golden_apb /workspace/golden_apb \
    && cp -r /app/examples/oran_ecpri /workspace/oran_ecpri

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python3 -c "import urllib.request,os,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/health',timeout=4).status==200 else 1)"

CMD ["sh", "-c", "uvicorn apps.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
