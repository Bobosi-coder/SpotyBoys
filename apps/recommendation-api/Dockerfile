FROM python:3.10-slim

WORKDIR /app
RUN pip install --no-cache-dir fastapi==0.92.0 uvicorn==0.20.0 pydantic==1.10.13 psycopg2-binary==2.9.9 redis==5.0.1 pyarrow==15.0.2 boto3==1.34.162 numpy==1.26.4 pandas==2.2.2 scipy==1.11.4 requests==2.31.0
RUN pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.2.2+cpu

COPY packages/ /app/packages/
COPY src/ /app/src/
COPY fixtures/ /app/fixtures/
COPY infra/ /app/infra/
COPY workers/ /app/workers/
COPY scripts/ /app/scripts/
COPY apps/recommendation-api/ /app/apps/recommendation-api/

ENV PYTHONPATH=/app
EXPOSE 8001
CMD ["uvicorn", "spotiboys_recommendation_api.main:app", "--host", "0.0.0.0", "--port", "8001", "--app-dir", "/app/apps/recommendation-api"]
