FROM alpine:3.20

RUN apk add --no-cache bash rsync sqlite

COPY entrypoint.sh /entrypoint.sh
COPY check-cloud-root.sh /usr/local/bin/check-cloud-root.sh
RUN chmod +x /entrypoint.sh /usr/local/bin/check-cloud-root.sh

CMD ["/entrypoint.sh"]
