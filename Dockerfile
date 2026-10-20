FROM python:3.13-alpine

COPY music_tools /app/music_tools

ENV PYTHONPATH=/app PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 1000:1000
ENTRYPOINT ["python", "-m", "music_tools"]
CMD ["deemix", "run"]
