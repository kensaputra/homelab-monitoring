FROM python:3.12-slim
WORKDIR /app
COPY bridge.py .
USER nobody
EXPOSE 8080
CMD ["python", "-u", "bridge.py"]
