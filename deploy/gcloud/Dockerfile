# syntax=docker/dockerfile:1.6
#
# Two-stage build for the sharktopus Cloud Run service:
#   stage 1: compile wgrib2 on Amazon Linux 2 with gfortran (reused from
#            the AWS image — identical compile recipe, different runtime)
#   stage 2: python:3.11-slim + wgrib2 + Flask HTTP wrapper
#
# Built and published by .github/workflows/build-image.yml to
# ghcr.io/sharktopus-project/sharktopus:cloudrun-<tag>. Users running
# ``sharktopus deploy gcloud`` pull the image from there into their own
# Artifact Registry / deploy to Cloud Run — no local Docker required.

# Builder and runtime share the same Debian (bookworm) base so the
# wgrib2 binary's libgfortran / libgomp ABI matches what the slim
# runtime ships. Mixing amazonlinux:2 (libgfortran.so.4) with Debian
# bookworm (libgfortran.so.5) silently produced a runtime loader
# failure — single-base avoids it.
FROM python:3.11-slim-bookworm AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential gfortran wget ca-certificates \
        zlib1g-dev libpng-dev libjpeg-dev && \
    rm -rf /var/lib/apt/lists/*

# wgrib2's sub-makefiles (proj-4.8.0, g2clib, etc.) invoke tar and
# choke on source-archive uid/gid — same shim used on the AWS image.
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

# -----------------------------------------------------------------------
# Runtime: python:3.11-slim-bookworm + wgrib2 + Flask wrapper
# -----------------------------------------------------------------------
FROM python:3.11-slim-bookworm

# wgrib2 runtime needs libgfortran + libgomp; add curl only for
# debugging / healthcheck convenience.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libgfortran5 libgomp1 curl && \
    rm -rf /var/lib/apt/lists/*

COPY --from=builder /tmp/grib2/wgrib2/wgrib2 /usr/local/bin/wgrib2
RUN chmod +x /usr/local/bin/wgrib2

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY main.py /app/main.py

# Cloud Run injects $PORT (default 8080). gunicorn serves the Flask app
# with enough workers to absorb parallel callers from batch mode.
ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind=:${PORT} --workers=1 --threads=8 --timeout=0 main:app
