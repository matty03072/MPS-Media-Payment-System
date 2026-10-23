FROM python:3.14.0-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPS_CONFIG=/state/config.json BIND_ADDRESS=0.0.0.0 PORT=8099
WORKDIR /app
COPY requirements.txt LICENSE ./
RUN pip install --no-cache-dir -r requirements.txt
COPY mps ./mps
COPY templates ./templates
COPY static ./static
COPY installer ./installer
COPY tests ./tests
COPY app.py serve.py container.py ./
USER 1000:1000
ENTRYPOINT ["python", "container.py"]
CMD ["web"]
