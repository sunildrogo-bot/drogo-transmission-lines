# --- drogo_trans_pilot: Flask app ---
FROM python:3.11-slim

# Runtime libs required by the bundled DJI Thermal SDK (.so files under
# vendor/dji_thermal_sdk) — libstdc++/libgomp/libgcc — plus build tools
# needed to compile psycopg2-binary/pyodbc wheels if no prebuilt wheel
# matches this platform, and unixODBC headers for pyodbc.
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

# Persisted at runtime via a Dokploy volume mount (see compose notes) so
# uploads/instance survive redeploys.
RUN mkdir -p instance static/uploads

EXPOSE 8000

CMD ["gunicorn", "--workers", "3", "--bind", "0.0.0.0:8000", "--timeout", "120", "app:app"]
