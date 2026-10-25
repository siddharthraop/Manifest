# MANIFEST: one image for the Cloud Run job (migrations) and the API service (ADR 0008).
#
#   job      python -m manifest.cloud                 (the default command)
#   service  uvicorn --factory api.main:create_app_from_env --host 0.0.0.0 --port $PORT
#
# Image size counts against Artifact Registry's 0.5 GiB free storage, shared by six products, so
# the Java runtime is cut down with jlink, DITA-OT loses its PDF renderers, and Babel keeps only
# English. Each cut was checked by running the full TPU migration on the cut-down toolchain.

ARG PYTHON=3.13-slim-trixie

# ---------------------------------------------------------------- Java: only the modules DITA-OT uses
FROM eclipse-temurin:21-jdk-noble AS jre
RUN jlink --add-modules java.base,java.compiler,java.datatransfer,java.desktop,java.instrument,java.logging,\
java.management,java.naming,java.net.http,java.prefs,java.scripting,java.security.jgss,java.sql,\
java.transaction.xa,java.xml,java.xml.crypto,jdk.crypto.ec,jdk.unsupported,jdk.xml.dom,jdk.zipfs,jdk.charsets \
      --strip-debug --no-man-pages --no-header-files --compress=zip-6 --output /opt/jre

# ---------------------------------------------------------------- DITA-OT 4.4 and lychee 0.24.2, checksummed
FROM eclipse-temurin:21-jdk-noble AS tools
ARG DITA_SHA256=598b9d405ed88112abb08a41189d750584e7eece86e89e97787777dea19401a0
ARG LYCHEE_SHA256=1f4e0ef7f6554a6ed33dd7ac144fb2e1bbed98598e7af973042fc5cd43951c9a
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*
ADD https://github.com/dita-ot/dita-ot/releases/download/4.4/dita-ot-4.4.zip /tmp/dita.zip
ADD https://github.com/lycheeverse/lychee/releases/download/lychee-v0.24.2/lychee-x86_64-unknown-linux-gnu.tar.gz /tmp/lychee.tgz
RUN echo "${DITA_SHA256}  /tmp/dita.zip" | sha256sum -c - \
 && echo "${LYCHEE_SHA256}  /tmp/lychee.tgz" | sha256sum -c - \
 && unzip -q /tmp/dita.zip -d /opt && mv /opt/dita-ot-4.4 /opt/dita-ot \
 && for p in org.dita.pdf2.axf org.dita.pdf2.xep org.dita.pdf2.fop com.elovirta.pdf; do /opt/dita-ot/bin/dita uninstall "$p"; done \
 && mkdir -p /opt/lychee && tar xzf /tmp/lychee.tgz -C /opt/lychee \
 && mv "$(find /opt/lychee -name lychee -type f | head -1)" /opt/lychee/lychee

# ---------------------------------------------------------------- Python dependencies
FROM python:${PYTHON} AS deps
WORKDIR /src
COPY pyproject.toml README.md ./
COPY manifest manifest
COPY api api
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir '.[sphinx,agent,cloud]' \
 && /opt/venv/bin/pip uninstall -y pip \
 && find /opt/venv -name __pycache__ -prune -exec rm -rf {} + \
 && find /opt/venv -path '*/babel/locale-data/*.dat' ! -name 'en*.dat' ! -name 'root.dat' -delete

# ---------------------------------------------------------------- runtime
FROM python:${PYTHON}
LABEL org.opencontainers.image.source="https://github.com/siddharthraop/Manifest" \
      org.opencontainers.image.licenses="MIT"
COPY --from=jre /opt/jre /opt/jre
COPY --from=tools /opt/dita-ot /opt/dita-ot
COPY --from=tools /opt/lychee/lychee /usr/local/bin/lychee
COPY --from=deps /opt/venv /opt/venv
ENV JAVA_HOME=/opt/jre DITA_HOME=/opt/dita-ot LYCHEE=/usr/local/bin/lychee MANIFEST_ROOT=/app \
    PATH=/opt/venv/bin:/opt/jre/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY corpus corpus
COPY rules rules
COPY recordings recordings
RUN useradd --uid 10001 --no-create-home manifest && chown -R manifest /opt/dita-ot
USER manifest
CMD ["python", "-m", "manifest.cloud"]
