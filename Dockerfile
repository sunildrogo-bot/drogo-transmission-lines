# --- drogo_trans_pilot: Flask app ---
FROM python:3.11-slim

# Runtime libraries for the bundled DJI Thermal SDK and Python DB drivers.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libstdc++6 \
    libgomp1 \
    libgcc-s1 \
    build-essential \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# These paths are mounted as persistent volumes in production.
RUN mkdir -p instance static/uploads

EXPOSE 8000

CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:8000", "--timeout", "120", "app:app"]

