FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd -m -s /bin/bash executor \
    && chown -R executor:executor /app

COPY --chown=executor:executor *.py ./
COPY --chown=executor:executor skills/ /app/skills/

USER executor

ENV SKILL_PATHS="/app/skills"
ENV SANDBOX_MODE="subprocess"
ENV LLM_PROVIDER="azure_openai"
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
