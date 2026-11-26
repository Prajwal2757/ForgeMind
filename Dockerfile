FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY forgemind forgemind
RUN useradd -m app && mkdir /data && chown app /data
USER app
ENV FORGEMIND_DB=/data/forgemind.db
EXPOSE 8000
CMD ["python", "-m", "forgemind", "serve", "--host", "0.0.0.0"]
