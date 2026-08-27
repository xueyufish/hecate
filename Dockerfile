FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
WORKDIR /tmp
# Build the wheel then install from it. WORKDIR /tmp keeps pip from
# re-reading /app/pyproject.toml during install. pip 25.0.1 still
# installs the package as editable (recording url=file:///app in
# dist-info/direct_url.json) and writes no package files into
# site-packages — see the COPY src/hecate below for the workaround.
# pip 25 also fails to install opentelemetry-instrumentation (a
# namespace package) at all when it's a transitive of opentelemetry-
# instrumentation-fastapi, which `from opentelemetry.instrumentation
# .fastapi import FastAPIInstrumentor` requires at runtime — so an
# explicit pip install line for the meta package is required.
RUN pip wheel --no-deps -w /tmp/wheels /app \
 && pip install --no-cache-dir --pre --force-reinstall /tmp/wheels/hecate-*.whl \
 && pip install --no-cache-dir --pre --force-reinstall --no-deps \
        opentelemetry-instrumentation opentelemetry-instrumentation-asgi \
        opentelemetry-instrumentation-fastapi wrapt \
        opentelemetry-semantic-conventions asgiref
WORKDIR /app
COPY . .
# Workaround for pip 25.0.1: pip's editable-install marker leaves only
# metadata in site-packages. COPY src/hecate so the package files exist
# alongside the dist-info and `import hecate` actually works at runtime.
COPY src/hecate /usr/local/lib/python3.12/site-packages/hecate
# Workaround for pip 25.0.1: pip's install logic writes only metadata
# (no package files) for many wheels, and worse, skips transitive deps
# like opentelemetry-instrumentation entirely. The cleanest cure is to
# not rely on `pip install` for file placement at all: download every
# wheel from the project's full dep tree and extract them straight into
# site-packages. Then every package has its files regardless of pip 25's
# quirks.
RUN python3 - <<'PYEOF'
import glob, os, site, subprocess, sys, zipfile
SP = site.getsitepackages()[0]
subprocess.run(
    [sys.executable, '-m', 'pip', 'download',
     '--no-cache-dir', '-d', '/tmp/hecate-fixwheels', '.[dev]'],
    check=True,
)
for whl in glob.glob('/tmp/hecate-fixwheels/*.whl'):
    with zipfile.ZipFile(whl) as z:
        z.extractall(SP)
PYEOF

FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app .

EXPOSE 8000

CMD ["uvicorn", "hecate.main:app", "--host", "0.0.0.0", "--port", "8000"]
