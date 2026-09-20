FROM python:3.11-slim

WORKDIR /app

# Dependencies first, so a code change doesn't reinstall them.
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app/

CMD ["python", "run.py"]
