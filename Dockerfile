FROM python:3.9-slim

RUN apt-get update && \
    apt-get install -y ffmpeg && \
    pip install audible-cli flask gunicorn psutil && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

RUN groupadd -g 1000 user && \
    useradd -m -u 1000 -g 1000 -s /bin/bash user

RUN mkdir -p /home/user/.audible && \
    chown -R user:user /home/user/.audible

WORKDIR /app
RUN mkdir -p /app/utils && \
    chown -R user:user /app

COPY config.py /app/
COPY app.py /app/
COPY routes.py /app/
COPY templates /app/templates/
COPY utils/*.py /app/utils/

RUN chown -R user:user /app

USER user

EXPOSE 5000

CMD ["gunicorn", "--workers=1", "--bind=0.0.0.0:5000", "--timeout=0", "app:app"]
