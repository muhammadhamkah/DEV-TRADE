#!/bin/sh
# Egress allowlist. The agent may talk only to the model API and Polymarket.
# Resolved at start; if a provider rotates IPs mid-run, restart the container.
set -e
ALLOW="${ALLOWED_HOSTS:-gamma-api.polymarket.com clob.polymarket.com}"
[ "${BACKEND:-anthropic}" = "anthropic" ] && ALLOW="$ALLOW api.anthropic.com"

iptables -F OUTPUT
iptables -A OUTPUT -o lo -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
for host in $ALLOW; do
  for ip in $(dig +short "$host" A | grep -E '^[0-9.]+$'); do
    iptables -A OUTPUT -d "$ip" -p tcp --dport 443 -j ACCEPT
  done
done
# Model backends that are not Anthropic: allow exactly the host and port in their URL.
for url in "$OLLAMA_URL" "$OPENAI_BASE_URL"; do
  [ -n "$url" ] || continue
  hostport=$(echo "$url" | sed -E 's#^[a-z]+://##; s#/.*$##')
  host=${hostport%%:*}
  port=${hostport##*:}
  [ "$port" = "$host" ] && port=443
  for ip in $(getent hosts "$host" | awk '{print $1}'); do
    iptables -A OUTPUT -d "$ip" -p tcp --dport "$port" -j ACCEPT
  done
  ALLOW="$ALLOW $host:$port"
done
iptables -P OUTPUT DROP
echo "egress locked to: $ALLOW"
