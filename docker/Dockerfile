FROM python:3.13-alpine AS base

ENV PATH=/app/.venv/bin:$PATH \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

RUN apk add --update --no-cache runuser uv
RUN addgroup -S bbblb \
 && adduser -u 1000 -s /bin/bash -S bbblb -G bbblb -H

FROM base AS app

WORKDIR /app

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project

# Install bbblb
ADD . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

VOLUME [ "/usr/share/bbblb" ]
ENV BBBLB_PATH_DATA=/usr/share/bbblb/

COPY docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT [ "/entrypoint.sh" ]

CMD [ "uvicorn", "--host", "0.0.0.0", "--port", "8000", "bbblb.asgi:app"]
