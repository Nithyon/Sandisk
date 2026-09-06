FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY . .

# The WM-811K pickle is mounted at runtime; it is intentionally not baked
# into the image or committed to Git.
ENV WM811K_PATH=/data/LSWMD.pkl

CMD ["python", "generate_data.py"]
