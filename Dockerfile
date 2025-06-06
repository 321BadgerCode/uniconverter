FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

# CMD ["python", "app.py", "--cleanup"]
ENV UNICONVERTER_CLEANUP=1
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:5000"]