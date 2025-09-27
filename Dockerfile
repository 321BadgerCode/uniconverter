FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y poppler-utils libimage-exiftool-perl
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

ENV UNICONVERTER_CLEANUP=1
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000"]
