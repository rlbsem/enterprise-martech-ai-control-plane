FROM python:3.12.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock pyproject.toml ./
RUN python -m pip install --no-cache-dir -r requirements.lock
COPY src ./src
RUN python -m pip install --no-deps --no-build-isolation .
COPY tests ./tests
COPY scripts ./scripts
COPY .github ./.github
COPY compose.yaml Dockerfile ./
RUN useradd --create-home --uid 10001 control && mkdir -p /app/docs/evidence && chown -R control:control /app
USER control
CMD ["python", "-m", "controlplane", "worker"]
