FROM python:3.12-slim
WORKDIR /app
COPY src/sharedcache.py /app/sharedcache.py
COPY cache-server/server.py /app/server.py
RUN useradd --uid 10001 --create-home cache && mkdir /data && chown cache:cache /data
USER cache
EXPOSE 8080
CMD ["python", "server.py"]
