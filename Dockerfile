FROM python:3.7-alpine

ADD . /opt/karakteraz/
WORKDIR /opt/karakteraz/

ENV GECKO_VERSION v0.21.0
ENV TZ Europe/Oslo

# install dependencies
RUN echo "http://dl-2.alpinelinux.org/alpine/edge/testing/" >> /etc/apk/repositories \
    && apk upgrade --update-cache --available --quiet \
    && apk add --no-cache --quiet \
        xvfb \
        firefox \
        dbus \
        py-pip \
        ttf-dejavu \
        tzdata \
    && cp /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone

# add geckodriver to path
RUN wget -q -P /opt/ https://github.com/mozilla/geckodriver/releases/download/$GECKO_VERSION/geckodriver-$GECKO_VERSION-linux64.tar.gz \
    && tar xfz /opt/geckodriver-$GECKO_VERSION-linux64.tar.gz \
    && mv geckodriver /usr/local/bin \
    && rm -f /opt/*.tar.gz

# install python requirements
RUN pip install --quiet --upgrade pip \
    && pip install --quiet -r requirements.txt \
    && pip install --quiet --upgrade selenium

CMD ["python", "./app.py"]