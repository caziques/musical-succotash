#!/bin/bash
# Test OpenWeatherMap API key and city
# Usage: ./test_weather.sh <api_key> <city>
# Example: ./test_weather.sh abc123def456 "Johannesburg,ZA"

KEY="${1:-e5230e8094823a62715334531f99616a}"
CITY="${2:-Johannesburg,ZA}"

echo "Testing: city=$CITY"
echo "Key: ${KEY:0:8}..."

RESPONSE=$(curl -s "https://api.openweathermap.org/data/2.5/weather?q=${CITY// /%20}&appid=${KEY}&units=metric")

if echo "$RESPONSE" | grep -q '"cod":401'; then
  echo "FAIL: Invalid API key"
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
  echo ""
  echo "Get a free key at: https://openweathermap.org/appid"
  exit 1
fi

if echo "$RESPONSE" | grep -q '"cod":404'; then
  echo "FAIL: City not found"
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
  exit 1
fi

if echo "$RESPONSE" | grep -q '"cod":200\|"id"'; then
  echo "OK: $(echo "$RESPONSE" | python3 -c "import json,sys; d=json.load(sys.stdin); print(f'{d[\"main\"][\"temp\"]}°C, {d[\"weather\"][0][\"description\"]}, {d[\"name\"]}')" 2>/dev/null)"
  echo ""
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null
else
  echo "UNEXPECTED:"
  echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
fi
