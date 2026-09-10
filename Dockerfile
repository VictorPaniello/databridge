FROM python:3.12-slim

# git is required here because tidycsv is installed from a GitHub URL, not
# from PyPI - pip needs git on PATH to clone it during the build.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
COPY alembic.ini ./
COPY alembic ./alembic

RUN pip install --no-cache-dir .

EXPOSE 8000

# TEMPORARY one-off fix (see CHANGELOG): Railway's database already has
# every table the baseline migration would create - an earlier deploy's
# Base.metadata.create_all() built it from the same models before Alembic
# was introduced. `alembic upgrade head` was therefore trying to
# CREATE TABLE users into a database that already has it and crash-looping
# on DuplicateTable. `stamp head` marks the migration as applied without
# re-running its DDL, which is correct here because the live schema and
# what the migration would create are identical. This line reverts to
# `alembic upgrade head` in the very next commit, once this has deployed
# successfully once.
CMD ["sh", "-c", "alembic stamp head && uvicorn databridge.main:app --host 0.0.0.0 --port 8000"]
