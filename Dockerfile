FROM python:3.11-slim

WORKDIR /app

# System-Fonts für ReportLab-Fallback
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Arbeitsverzeichnisse anlegen
RUN mkdir -p orders fonts

CMD ["python", "main.py"]
