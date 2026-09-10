FROM python:3.12-slim

# git is required here because tidycsv is installed from a GitHub URL, not
# from PyPI - pip needs git on PATH to clone it during the build.
#
# postgresql-client-18: bookworm's default apt package is only v15, but
# Railway's Postgres is v18.6 (confirmed via `SHOW server_version` in
# Railway's Console against the live database) - pg_dump generally refuses
# to dump a server newer than itself, so the PGDG apt repo is added here to
# install a version-matched client instead of relying on Debian's stock one.
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        gnupg \
        ca-certificates \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc --fail \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-18 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY scripts ./scripts
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

EXPOSE 8000

# Runs pending migrations before starting the server - schema is
# Alembic-managed (see alembic/), not created ad hoc by the app itself.
#
# --proxy-headers + --forwarded-allow-ips='*': Railway terminates TLS at
# its own edge and forwards plain HTTP to this container, with the real
# scheme in the X-Forwarded-Proto header. Without these flags uvicorn
# ignores that header and reports every request as http://, which broke
# GitHub OAuth's callback URL matching (generated http://..., registered
# https://... on GitHub - a mismatch GitHub would reject). '*' is safe
# here because Railway's own proxy is the only thing that can reach this
# container - nothing external talks to it directly.
CMD ["sh", "-c", "alembic upgrade head && uvicorn databridge.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
