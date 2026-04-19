# syntax=docker/dockerfile:1.6
#
# Two-stage build for the sharktopus Lambda:
#   stage 1: compile wgrib2 on Amazon Linux 2 with gfortran
#   stage 2: AWS Lambda Python base image + wgrib2 binary + handler
#
# Built and pushed by deploy/aws/provision.py. Users running
# ``sharktopus deploy aws`` in their own account will either build this
# image locally (if Docker is present) or pull the prebuilt image from
# a public registry (GHCR, once the publish workflow lands).

FROM public.ecr.aws/amazonlinux/amazonlinux:2 AS builder

RUN yum -y update && \
    yum -y install \
        gcc gcc-gfortran make wget tar gzip \
        zlib-devel libpng-devel jasper-devel libjpeg-turbo-devel && \
    yum clean all

# Wgrib2's sub-makefiles extract proj/jasper/etc. with tar. When the
# source tarball has uid/gid that don't exist in the builder image
# (common in rootless / cross-host builds), tar aborts trying to chown.
# Force --no-same-owner globally via a shim.
RUN mv /usr/bin/tar /usr/bin/tar_bin && \
    printf '#!/bin/sh\nexec /usr/bin/tar_bin --no-same-owner "$@"\n' > /usr/bin/tar && \
    chmod +x /usr/bin/tar

ENV FC=gfortran CC=gcc USE_NETCDF3=0 USE_NETCDF4=0

WORKDIR /tmp
RUN wget -q https://ftp.cpc.ncep.noaa.gov/wd51we/wgrib2/wgrib2.tgz && \
    tar -xzf wgrib2.tgz && rm -f wgrib2.tgz

WORKDIR /tmp/grib2
RUN sed -i -e 's/^USE_AEC=1/USE_AEC=0/' -e 's/^USE_OPENJPEG=1/USE_OPENJPEG=0/' makefile && \
    make

# -----------------------------------------------------------------------
# Runtime: Lambda Python 3.11 base + wgrib2 + handler
# -----------------------------------------------------------------------
FROM public.ecr.aws/lambda/python:3.11

RUN yum -y install libgfortran libgomp && yum clean all

COPY --from=builder /tmp/grib2/wgrib2/wgrib2 /usr/local/bin/wgrib2
RUN chmod +x /usr/local/bin/wgrib2

# boto3 is already bundled in the Lambda Python image, but botocore is
# pinned to what comes with the runtime. We don't install anything extra.

COPY handler.py ${LAMBDA_TASK_ROOT}/handler.py

CMD ["handler.lambda_handler"]
