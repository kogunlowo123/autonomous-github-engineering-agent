# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist

FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    GHAGENT_WORK_DIR=/data/work \
    GHAGENT_OUT_DIR=/data/runs
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 ghagent \
    && mkdir /data \
    && chown ghagent /data
COPY --from=build /dist/*.whl /tmp/
RUN pip install /tmp/*.whl && rm /tmp/*.whl
USER ghagent
WORKDIR /home/ghagent
VOLUME ["/data"]
ENTRYPOINT ["ghagent"]
CMD ["policy"]
