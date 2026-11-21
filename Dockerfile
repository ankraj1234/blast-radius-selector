# The scorer builds this on linux/amd64 and runs it with no network.
FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

RUN apt-get update \
    && apt-get install -y --no-install-recommends git patch \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock pytest-xdist pytest-timeout

RUN git init /opt/sqlglot \
    && cd /opt/sqlglot \
    && git remote add origin https://github.com/tobymao/sqlglot.git \
    && git fetch --depth 1 origin eb11aec4be8cb2122afe713a4816662c3f0fb2fa \
    && git checkout FETCH_HEAD

COPY build_map.py /tmp/build_map.py
RUN cd /opt/sqlglot && pip install --no-cache-dir -e . && python /tmp/build_map.py

COPY selector.py /app/selector.py
CMD ["python", "/app/selector.py"]
