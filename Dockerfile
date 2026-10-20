FROM python:3.13-alpine

COPY deemix_helper /app/deemix_helper

ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 1000:1000
ENTRYPOINT ["python", "-m", "deemix_helper"]
CMD ["run"]
