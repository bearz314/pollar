# Use a Python base image
FROM python:3.12-slim

# Install dependencies
COPY ./requirements.txt /tmp/requirements.txt
WORKDIR /tmp
RUN pip install -r requirements.txt

# Copy app to container image
COPY ./app/ /app
WORKDIR /app

# Run the web service on container startup, use gevent with 1 worker to enable websocket
CMD ["gunicorn", "-k", "gevent", "-w", "1",  "-b", ":8000", "backend:app"]
