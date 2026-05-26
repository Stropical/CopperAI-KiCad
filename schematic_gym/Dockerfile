FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libcairo2 \
    fontconfig \
    fonts-dejavu-core \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app/schematic_gym/
RUN pip install --no-cache-dir /app/schematic_gym

CMD ["python", "-c", "import schematic_gym; print('SchematicGym ready')"]
