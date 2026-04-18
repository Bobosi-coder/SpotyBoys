FROM python:3.10-slim

WORKDIR /app
COPY apps/frontend-web/ /app/

EXPOSE 5173
CMD ["python", "-m", "http.server", "5173", "--bind", "0.0.0.0"]
