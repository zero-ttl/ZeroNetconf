FROM python:3.9-alpine

WORKDIR /
COPY . .
RUN pip install -r requirements.txt
#COPY ./config ./config
#COPY ./zmTools ./zmTools

RUN chmod u+x /zmTools/zeronetconf.py

#ENTRYPOINT ["/zmTools/zeronetconf.py"]
#CMD  ["/bin/sh", "-c", "python zmTools/zeronetconf.py  > zeronetconf.log 2>&1"]
CMD  ["/bin/sh", "-c", "python zmTools/getBgpAdvPrefixes.py "]