FROM python:3.12-slim
WORKDIR /app
COPY relay.py .
USER nobody
EXPOSE 8747
CMD ["python", "-u", "relay.py"]
