# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-builder
ARG APP_VERSION
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
ENV VITE_APP_VERSION=${APP_VERSION}
RUN npm run build

FROM python:3.12-slim AS python-builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY migrations/ ./migrations/
COPY src/ ./src/
RUN python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ARG APP_VERSION
ARG GIT_COMMIT
ARG BUILD_TIMESTAMP
ARG OFFLINE_BASELINE_ID
LABEL org.opencontainers.image.title="CommerceResolve" \
      org.opencontainers.image.version=${APP_VERSION} \
      org.opencontainers.image.revision=${GIT_COMMIT} \
      org.opencontainers.image.source="https://github.com/coratch/commerce-resolve-agent"

RUN test -n "${APP_VERSION}" \
    && test -n "${GIT_COMMIT}" \
    && test -n "${BUILD_TIMESTAMP}" \
    && test -n "${OFFLINE_BASELINE_ID}" \
    && groupadd --gid 10001 commerce-resolve \
    && useradd --uid 10001 --gid 10001 --no-create-home commerce-resolve \
    && mkdir -p /app/frontend /app/data /var/lib/commerce-resolve \
    && chown -R 10001:10001 /var/lib/commerce-resolve

WORKDIR /app
COPY requirements.runtime.lock ./requirements.runtime.lock
RUN python -m pip install --no-cache-dir -r requirements.runtime.lock
COPY --from=python-builder /wheels/*.whl /wheels/
RUN python -m pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels
COPY --from=frontend-builder /build/frontend/dist ./frontend/dist
COPY data/policies/ ./data/policies/
COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY requirements.runtime.lock ./requirements.runtime.lock
RUN python -m commerce_resolve.operations.manifest \
      --project-root /app \
      --app-version "${APP_VERSION}" \
      --git-commit "${GIT_COMMIT}" \
      --build-timestamp "${BUILD_TIMESTAMP}" \
      --frontend-dist /app/frontend/dist \
      --baseline-id "${OFFLINE_BASELINE_ID}" \
      --output /app/release-manifest.json \
    && python -m pip check

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "-m", "commerce_resolve"]
CMD ["serve"]
