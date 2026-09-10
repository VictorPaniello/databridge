FROM python:3.12-slim

# git is required here because tidycsv is installed from a GitHub URL, not
# from PyPI - pip needs git on PATH to clone it during the build.
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "databridge.main:app", "--host", "0.0.0.0", "--port", "8000"]
