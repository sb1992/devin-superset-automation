FROM python:3.12-slim

WORKDIR /app
# GitHub container actions override the working directory to /github/workspace;
# keep the package importable from any cwd.
ENV PYTHONPATH=/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY schemas/ schemas/
COPY fixtures/ fixtures/

ENTRYPOINT ["python", "-m", "src.main"]
