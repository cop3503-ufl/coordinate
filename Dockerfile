FROM python:3.12-alpine
WORKDIR /app

RUN apk update

# We need to install git for the gradescope-api requirement (which uses github)
RUN apk add git
# We need to install gcc for matplotlib
RUN apk add build-base

# Copy only the requirements file first (for caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy everything else
COPY . .
CMD ["python3", "-m", "src.bot"]
