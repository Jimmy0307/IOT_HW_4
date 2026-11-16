# Eclipse Mosquitto (MQTT 5) with a hardened entrypoint.
# Secrets are bind-mounted read-only under /run/orchid-secrets and copied into a
# private runtime directory (0700, owner mosquitto) so Mosquitto never reads
# world-readable key/password material from a host bind mount.
FROM eclipse-mosquitto:2.1.2-alpine

COPY docker-entrypoint.sh /orchid/docker-entrypoint.sh
RUN chmod 0755 /orchid/docker-entrypoint.sh \
 && mkdir -p /mosquitto/runtime /mosquitto/data \
 && chown -R mosquitto:mosquitto /mosquitto/runtime /mosquitto/data

HEALTHCHECK --interval=10s --timeout=8s --start-period=10s --retries=6 \
  CMD XDG_CONFIG_HOME=/mosquitto/runtime/xdg mosquitto_sub -t '$SYS/broker/uptime' -C 1 -W 5 >/dev/null || exit 1

ENTRYPOINT ["/orchid/docker-entrypoint.sh"]
