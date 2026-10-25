# One image for both surfaces.
#   Board / judge mode (Serverless Endpoint):  default CMD
#   Nightly check (Serverless Job):            command: python -m donor_lookback.job
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000 DONOR_LOOKBACK_DB=/data/lookback.db DONOR_LOOKBACK_DEMO_STATE=/data/demo_page.json
VOLUME ["/data"]
CMD ["sh", "-c", "uvicorn donor_lookback.api:app_from_env --factory --host 0.0.0.0 --port ${PORT}"]
