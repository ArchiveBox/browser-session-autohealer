"""HTTP control plane. Returned CDP URLs belong directly to provider sessions."""
import asyncio
import time
from uuid import UUID

from plain.http import JsonResponse, NotFoundError404
from plain.postgres.db import return_database_connection

from .api import API
from .core import session_broker as broker
from .core.models import SessionRequest


class SessionsAPI(API):
    def lookup(self):
        row = SessionRequest.query.filter(uid=UUID(self.url_kwargs['uid'])).first()
        if row is None:
            raise NotFoundError404()
        return row

    def handle_exception(self, exc):
        if isinstance(exc, (ValueError, TypeError, KeyError)):
            return JsonResponse({'type': '/problems/invalid-session-request', 'title': str(exc), 'status': 400},
                                status_code=400, content_type='application/problem+json')
        return super().handle_exception(exc)

    def get(self):
        if self.url_kwargs.get('uid'):
            return self.response(self.lookup())
        return JsonResponse({'requests': [broker.state(r) for r in SessionRequest.query.order_by('-id')[:100]]},
                            headers={'Cache-Control': 'no-store'})

    async def post(self):
        if self.url_kwargs.get('uid'):
            request = self.lookup()
            data = self.request.json_data
            if not isinstance(data, dict):
                raise TypeError('A JSON object is required')
            if set(data) - {'success', 'message'}:
                raise ValueError('Release accepts only success and message')
            return self.response(broker.release(request.id, **data))
        request = broker.create(self.request.json_data, self.request.headers.get('Idempotency-Key'))
        timeout = request.spec['timeout']
        # -1 is durable pending work, not an infinite HTTP socket. respond-async
        # opts out of HTTP waiting without changing the readiness deadline.
        wait = min(timeout, 60) if timeout > 0 else 0
        if 'respond-async' in self.request.headers.get('Prefer', ''):
            wait = 0
        end = time.monotonic() + wait
        while request.status not in broker.TERMINAL | {'ready'} and time.monotonic() < end:
            return_database_connection()
            await asyncio.sleep(.25)
            request = SessionRequest.query.get(id=request.id)
        return self.response(request)

    def response(self, request):
        request = broker.expire(request)
        result = broker.state(request)
        headers = {'Cache-Control': 'no-store', 'Location': result['url']}
        if request.status == 'failed':
            return JsonResponse({'type': '/problems/session-unavailable', 'title': 'No usable session is available',
                                 'status': 409, 'request': result}, status_code=409,
                                content_type='application/problem+json', headers=headers)
        return JsonResponse(result, status_code=200 if request.status in broker.TERMINAL | {'ready'} else 202,
                            headers=headers)
