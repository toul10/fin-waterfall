FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOSTED_BIND=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY deploy/python313.constraints.txt deploy/hosted.constraints.txt /opt/constraints/
RUN python -c "import tomllib; p=tomllib.load(open('pyproject.toml','rb'))['project']; print(*p['dependencies'], *p['optional-dependencies']['hosted'], sep='\n')" > /tmp/requirements.txt && pip install --no-cache-dir -c /opt/constraints/python313.constraints.txt -c /opt/constraints/hosted.constraints.txt -r /tmp/requirements.txt
COPY abs_waterfall_check ./abs_waterfall_check
RUN pip install --no-cache-dir --no-deps . && useradd --create-home --uid 10001 waterfall
USER 10001
CMD ["python", "-m", "abs_waterfall_check.hosted"]
