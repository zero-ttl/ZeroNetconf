FROM python:3.9-alpine

WORKDIR /
COPY . .
RUN pip install -r requirements.txt

CMD  ["/bin/sh", "-c", "python zeronetconf.py "]