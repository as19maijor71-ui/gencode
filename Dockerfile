FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml cardgen/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY cardgen/ ./cardgen/

CMD ["python", "-m", "cardgen.bot.main"]
