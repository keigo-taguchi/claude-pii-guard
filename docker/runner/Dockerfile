FROM python:3.12-slim
RUN pip install --no-cache-dir pandas==2.2.3 numpy==2.1.3 pyarrow==18.1.0 openpyxl==3.1.5
RUN useradd -m -u 10001 runner
COPY sd_runner.py /runner/sd_runner.py
USER runner
WORKDIR /tmp
ENTRYPOINT []
