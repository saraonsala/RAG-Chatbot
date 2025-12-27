# utils/rate_limiter.py

"""
Enkel rate limiter för API-endpoints.
Implementerar en sliding window för att begränsa antalet förfrågningar per IP.
"""

import time
import logging
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger(__name__)


class SimpleRateLimiter:
    """
    En enkel minnesbaserad rate limiter som använder sliding window.
    OBS: I produktion bör du använda Redis eller liknande för distribuerade system.
    """

    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        """
        Args:
            max_requests: Max antal förfrågningar tillåtna
            window_seconds: Tidsfönster i sekunder
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # Lagrar timestamps för varje IP: {ip: deque([timestamp1, timestamp2, ...])}
        self.requests: Dict[str, Deque[float]] = defaultdict(deque)

    def _get_client_ip(self) -> str:
        """Hämtar klientens IP-adress från Flask request."""
        # Försök hämta från proxy headers först
        if request.headers.get('X-Forwarded-For'):
            return request.headers.get('X-Forwarded-For').split(',')[0].strip()
        elif request.headers.get('X-Real-IP'):
            return request.headers.get('X-Real-IP')
        else:
            return request.remote_addr or 'unknown'

    def _clean_old_requests(self, ip: str, current_time: float) -> None:
        """Rensar förfrågningar som ligger utanför tidsfönstret."""
        cutoff_time = current_time - self.window_seconds

        # Ta bort gamla timestamps från vänster (äldst)
        while self.requests[ip] and self.requests[ip][0] < cutoff_time:
            self.requests[ip].popleft()

    def is_allowed(self, ip: str = None) -> Tuple[bool, Dict]:
        """
        Kontrollerar om en förfrågan är tillåten.

        Args:
            ip: IP-adress att kontrollera (om None, hämtas från request)

        Returns:
            Tuple av (är_tillåten, info_dict)
            info_dict innehåller: remaining, reset_time, retry_after
        """
        if ip is None:
            ip = self._get_client_ip()

        current_time = time.time()

        # Rensa gamla förfrågningar
        self._clean_old_requests(ip, current_time)

        # Räkna aktuella förfrågningar i fönstret
        request_count = len(self.requests[ip])

        if request_count < self.max_requests:
            # Tillåt förfrågan och logga tidsstämpel
            self.requests[ip].append(current_time)
            remaining = self.max_requests - request_count - 1

            return True, {
                'remaining': remaining,
                'limit': self.max_requests,
                'reset_time': int(current_time + self.window_seconds)
            }
        else:
            # Rate limit nådd
            oldest_request = self.requests[ip][0]
            retry_after = int(oldest_request + self.window_seconds - current_time + 1)

            logger.warning(f"⚠️ Rate limit exceeded for IP: {ip}")

            return False, {
                'remaining': 0,
                'limit': self.max_requests,
                'retry_after': retry_after,
                'reset_time': int(oldest_request + self.window_seconds)
            }

    def cleanup_stale_ips(self, max_age_seconds: int = 3600) -> int:
        """
        Rensar IP-adresser som inte har gjort förfrågningar på länge.
        Körs periodiskt för att undvika minnesläckor.

        Args:
            max_age_seconds: Max ålder för att behålla IP-data

        Returns:
            Antal IP-adresser som rensades
        """
        current_time = time.time()
        cutoff_time = current_time - max_age_seconds
        ips_to_remove = []

        for ip, timestamps in self.requests.items():
            if not timestamps or timestamps[-1] < cutoff_time:
                ips_to_remove.append(ip)

        for ip in ips_to_remove:
            del self.requests[ip]

        if ips_to_remove:
            logger.info(f"🧹 Cleaned up {len(ips_to_remove)} stale IP entries from rate limiter")

        return len(ips_to_remove)


def rate_limit(limiter: SimpleRateLimiter):
    """
    Decorator för Flask routes som tillämpar rate limiting.

    Usage:
        limiter = SimpleRateLimiter(max_requests=10, window_seconds=60)

        @app.route('/api/endpoint')
        @rate_limit(limiter)
        def my_endpoint():
            return jsonify({"status": "ok"})
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            allowed, info = limiter.is_allowed()

            if not allowed:
                response = jsonify({
                    "error": "Rate limit exceeded",
                    "message": f"Too many requests. Please try again in {info['retry_after']} seconds.",
                    "retry_after": info['retry_after'],
                    "limit": info['limit']
                })
                response.status_code = 429
                response.headers['Retry-After'] = str(info['retry_after'])
                response.headers['X-RateLimit-Limit'] = str(info['limit'])
                response.headers['X-RateLimit-Remaining'] = '0'
                response.headers['X-RateLimit-Reset'] = str(info['reset_time'])
                return response

            # Lägg till rate limit headers i svaret
            response = f(*args, **kwargs)

            # Om response är en tuple (response, status_code), hantera det
            if isinstance(response, tuple):
                actual_response = response[0]
            else:
                actual_response = response

            # Lägg till headers (fungerar för både jsonify och andra response-typer)
            if hasattr(actual_response, 'headers'):
                actual_response.headers['X-RateLimit-Limit'] = str(info['limit'])
                actual_response.headers['X-RateLimit-Remaining'] = str(info['remaining'])
                actual_response.headers['X-RateLimit-Reset'] = str(info['reset_time'])

            return response

        return decorated_function
    return decorator
