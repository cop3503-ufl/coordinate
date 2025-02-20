FROM python:3.12-alpine
WORKDIR /app

# We need to install git for the gradescope-api requirement (which uses github)
# We need to install gcc for matplotlib
RUN apk add --no-cache \
    git=2.47.2-r0 \
    build-base=0.5-r3

# Copy only the requirements file first (for caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything else
COPY . .
CMD ["python3", "-m", "src.bot"]
