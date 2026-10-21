FROM python:3.11-slim

# pinned dependency set first, so code changes don't invalidate this layer
COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY pyproject.toml /tmp/src/
COPY sensei_service /tmp/src/sensei_service
RUN pip install --no-cache-dir --no-deps /tmp/src && rm -rf /tmp/src /tmp/requirements.lock

RUN useradd --create-home sensei
USER sensei
WORKDIR /home/sensei

EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"]
CMD ["uvicorn", "sensei_service.main:app", "--host", "0.0.0.0", "--port", "8000"]
