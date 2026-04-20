# syntax=docker/dockerfile:1.6
#
# Container Apps build for the sharktopus crop service.
#
# Identical builder + runtime to deploy/gcloud/Dockerfile — wgrib2 on
# python:3.11-slim-bookworm + Flask. Container Apps and Cloud Run both
# accept the same OCI contract (HTTP server on $PORT), so the only
# variant is the requirements file (azure-storage-blob instead of
# google-cloud-storage) and the handler entry point.
#
# Built and published by .github/workflows/build-image.yml to
# ghcr.io/sharktopus-project/sharktopus:azure-<tag>. Users running
# ``sharktopus --setup azure`` deploy this image straight to Container
# Apps via the Azure SDK — no local Docker required.

FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential gfortran wget ca-certificates \
        zlib1g-dev libpng-dev libjpeg-dev && \
    rm -rf /var/lib/apt/lists/*

RUN mv /usr/bin/tar /usr/bin/tar_bin && \
    printf '#!/bin/sh\nexec /usr/bin/tar_bin --no-same-owner "$@"\n' > /usr/bin/tar && \
    chmod +x /usr/bin/tar

ENV FC=gfortran CC=gcc USE_NETCDF3=0 USE_NETCDF4=0

WORKDIR /tmp
RUN wget -q https://ftp.cpc.ncep.noaa.gov/wd51we/wgrib2/wgrib2.tgz && \
    tar -xzf wgrib2.tgz && rm -f wgrib2.tgz

WORKDIR /tmp/grib2
RUN sed -i -e 's/^USE_AEC=1/USE_AEC=0/' \
           -e 's/^USE_OPENJPEG=1/USE_OPENJPEG=0/' \
           -e 's/^USE_JASPER=1/USE_JASPER=0/' \
           makefile && \
    make

FROM python:3.11-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends libgfortran5 libgomp1 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /tmp/grib2/wgrib2/wgrib2 /usr/local/bin/wgrib2
RUN chmod +x /usr/local/bin/wgrib2

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py

# Container Apps injects $PORT (default 8080); same contract as Cloud Run.
ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind=:${PORT} --workers=1 --threads=8 --timeout=0 main:app
