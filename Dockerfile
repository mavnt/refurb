FROM python:3.10
WORKDIR /tmp
COPY . /tmp
RUN pip install --no-cache-dir . && rm -rf /tmp/*
WORKDIR /workdir
ENTRYPOINT [ "python3", "-m", "refurb"]
