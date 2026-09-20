# The benchmark needs no Minecraft: every case is already recorded.
FROM python:3.12-slim
WORKDIR /app
COPY . /app
# TYPESAFE_API_KEY is supplied at run time; nothing is baked into the image.
CMD ["python3", "/app/benchmark/score.py"]
