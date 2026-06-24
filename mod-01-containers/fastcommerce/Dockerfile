FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    libpq-dev \
    gcc \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Collect static files (if needed)
RUN python manage.py collectstatic --noinput --settings=fastcommerce.settings.base

# Expose the application port
EXPOSE 8000

# Start the application
CMD ["gunicorn", "fastcommerce.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]
