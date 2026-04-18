FROM python:3.10-slim

WORKDIR /app
COPY packages/ /app/packages/
COPY fixtures/ /app/fixtures/
COPY apps/event-api/ /app/apps/event-api/

RUN pip install --no-cache-dir fastapi==0.92.0 uvicorn==0.20.0 pydantic==1.10.13 psycopg2-binary==2.9.9 redis==5.0.1

ENV PYTHONPATH=/app
EXPOSE 8002
CMD ["uvicorn", "spotiboys_event_api.main:app", "--host", "0.0.0.0", "--port", "8002", "--app-dir", "/app/apps/event-api"]
