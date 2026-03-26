# One x86_64 image for every Python Lambda. Each function selects its handler with CMD at deploy
# time; the image itself is handler-agnostic. `--platform linux/amd64` is pinned explicitly so
# the build is reproducible on an arm64 dev machine too — there is no goal of developing,
# testing, or deploying on arm64 anywhere in this project.
#
# Build context is the repository root, e.g.:
#   docker build -f services/Dockerfile --build-arg SERVICE=api -t cwd-api .

ARG BASE_PLATFORM=linux/amd64

FROM --platform=$BASE_PLATFORM public.ecr.aws/lambda/python:3.12 AS build

RUN pip install --no-cache-dir uv==0.9.7

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY services/common/pyproject.toml services/common/pyproject.toml
COPY services/api/pyproject.toml services/api/pyproject.toml
COPY services/ingestion/pyproject.toml services/ingestion/pyproject.toml
COPY services/answering/pyproject.toml services/answering/pyproject.toml

ARG SERVICE
RUN uv sync --frozen --no-dev --no-editable --no-install-workspace --package cwd-${SERVICE}

COPY services/common services/common
COPY services/${SERVICE} services/${SERVICE}
RUN uv sync --frozen --no-dev --no-editable --package cwd-${SERVICE}

# Lambda's Python base image puts LAMBDA_TASK_ROOT itself on sys.path (it is not a venv), so the
# venv's site-packages are flattened into it rather than the venv copied whole.
FROM --platform=$BASE_PLATFORM public.ecr.aws/lambda/python:3.12

COPY --from=build /build/.venv/lib/python3.12/site-packages/ ${LAMBDA_TASK_ROOT}/

# Overridden per function, e.g. CMD ["api.handler.lambda_handler"]
CMD ["common.config.get_settings"]
