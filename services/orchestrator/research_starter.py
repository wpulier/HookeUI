"""Research link previews are read-only. Accepted briefs travel in their Project's first journal event."""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from research_worker import safe_fetch
from . import scientific_url_intake as public_web

CHALLENGE = 'https://lightmycells2.grand-challenge.org/'
ORGANIZER = 'https://france-bioimaging.org/services/data-services/challenge/light-my-cells-2-description/'
TITLE = 'Light My Cells 2'
QUESTION = 'Can we predict what would fluoresce from an unlabeled image of a cell?'
TASKS = ('Predict an in-focus fluorescence image from a single transmitted-light image.\n'
         'Predict a fluorescence maximum-intensity projection from a transmitted-light image stack.')
CONTEXT = ('Register for the training data, then add a transmitted-light input and its matching '
           'fluorescence reference image to this Project.')
_PREVIEW_KEY = secrets.token_bytes(32)
LIMITS = {'source': 2048, 'title': 80, 'question': 800, 'context': 2500, 'original': 2500}


def web_url(value):
    if not isinstance(value, str) or not 0 < len(value) <= LIMITS['source'] or any(
            ch.isspace() or ord(ch) < 32 or ord(ch) == 127 or ch == '\\' for ch in value):
        raise ValueError('Enter a complete public web address.')
    parts = urlsplit(value)
    if (parts.scheme not in ('http', 'https') or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.port == 0):
        raise ValueError('Use an http or https address without sign-in credentials.')
    return parts


def featured(url):
    parts = web_url(url)
    return ((parts.hostname == 'lightmycells2.grand-challenge.org' and
             parts.path.rstrip('/') in ('', '/lightmycells2')) or
            (parts.hostname == 'france-bioimaging.org' and
             parts.path.rstrip('/') == urlsplit(ORGANIZER).path.rstrip('/'))) and not parts.port


def _receipt(brief):
    # Only the source's identity and fetched excerpt are immutable; title, question and notes are editable.
    payload = json.dumps([brief['source'], brief['status'], brief['original']], ensure_ascii=False).encode()
    return hmac.digest(_PREVIEW_KEY, payload, 'sha256').hex()


class _PublicPageTransport(httpx.AsyncHTTPTransport):
    """Recheck each HTTP hop and dial a validated numeric address, keeping the original TLS name."""
    async def handle_async_request(self, request):
        host = request.url.raw_host.decode('ascii')
        port = request.url.port or (443 if request.url.scheme == 'https' else 80)
        try:
            addresses = await asyncio.to_thread(public_web.public, host, port)
        except public_web.RemoteRefused as error:
            raise safe_fetch.FetchRefused(str(error)) from error
        # Prefer IPv4 when both families exist. Neither HTTP nor TLS resolves host again.
        address = min(addresses, key=lambda ip: (':' in ip, ip))
        headers = {'Host': request.url.netloc.decode('ascii'), 'User-Agent': safe_fetch.USER_AGENT,
                   'Accept-Encoding': 'identity', 'Accept': 'text/html, application/xhtml+xml, text/plain'}
        pinned = httpx.Request('GET', request.url.copy_with(host=address), headers=headers,
                               extensions={**request.extensions, 'sni_hostname': host})
        response = await super().handle_async_request(pinned)
        if response.headers.get('content-encoding', 'identity').lower() not in ('', 'identity'):
            await response.aclose()
            raise safe_fetch.FetchRefused('The site did not provide an uncompressed preview.')
        return response


async def preview(url):
    parts = web_url(url)
    if featured(url):
        return {'title': TITLE, 'question': QUESTION, 'source': CHALLENGE,
                'original': TASKS, 'context': CONTEXT, 'status': 'featured'}
    starter = {'source': url, 'title': parts.hostname[:80], 'question': '', 'context': '',
               'original': '', 'status': 'unreadable'}
    try:
        # A new connection for each origin preserves the correct SNI when two sites share an IP.
        transport = _PublicPageTransport(trust_env=False, limits=httpx.Limits(max_keepalive_connections=0))
        async with httpx.AsyncClient(transport=transport, trust_env=False, timeout=safe_fetch.TIMEOUT_S) as client:
            page = await safe_fetch.fetch_page_preview(url, client=client)
    except safe_fetch.FetchRefused:
        return starter
    if page['text']:
        starter.update(title=page['title'][:80] or starter['title'], original=page['text'][:2500],
                       context=page['description'][:2500], status='read')
        if starter['title'].endswith('?'):
            starter['question'] = starter['title']
        starter['receipt'] = _receipt(starter)
    return starter


def accepted(body, request):
    if not isinstance(body, dict) or (set(body) - {'receipt'}) != ({'status'} | LIMITS.keys()):
        raise ValueError('Review the research starter again.')
    if not isinstance(request, str) or str(UUID(request)) != request:
        raise ValueError('The research starter has no valid request identity.')
    if not all(isinstance(body[key], str) and len(body[key]) <= limit for key, limit in LIMITS.items()):
        raise ValueError('Shorten the title or research brief before creating this Project.')
    web_url(body['source'])
    if body['status'] not in ('featured', 'read', 'unreadable') or not body['title'].strip():
        raise ValueError('Give this Project a title and review the source.')
    if body['status'] == 'featured' and (body['source'] != CHALLENGE or body['original'] != TASKS):
        raise ValueError('Review the official challenge brief again.')
    if body['status'] == 'unreadable' and body['original']:
        raise ValueError('A page that could not be read cannot supply a source excerpt.')
    # A header is kept in one bounded journal record, including escaped non-ASCII text.
    if len(json.dumps(body).encode()) > 60000:
        raise ValueError('The research brief is too large to keep. Shorten the text and try again.')
    return {**{key: value for key, value in body.items() if key != 'receipt'}, 'requestId': request}


def verify_preview(starter, receipt):
    if starter['status'] == 'read' and (
            not isinstance(receipt, str) or len(receipt) != 64 or not receipt.isascii()
            or not hmac.compare_digest(_receipt(starter), receipt)):
        raise ValueError('The source excerpt could not be verified. Preview the link again.')


def register(api, built, studio_origin, header):
    def window_allowed(request):
        token = os.environ.get('HOOKE_STUDIO_TOKEN')
        return not token or secrets.compare_digest(token, request.headers.get('x-hooke-studio', ''))

    @api.post('/api/research-starter/preview')
    async def preview_route(request: Request):
        if (not window_allowed(request) or request.headers.get(header) != 'preview-research-link'
                or not studio_origin(request.headers.get('origin'), request.url.port)):
            return JSONResponse({'error': 'Preview a research link from the Studio.'}, status_code=403)
        try:
            data = bytearray()
            async for chunk in request.stream():
                if len(data) + len(chunk) > 16384:
                    return JSONResponse({'error': 'The research URL is too long.'}, status_code=413)
                data.extend(chunk)
            body = json.loads(data)
            starter = await preview(body.get('url') if isinstance(body, dict) else None)
        except ValueError as error:
            return JSONResponse({'error': str(error)}, status_code=400)
        return JSONResponse(starter, headers={'Cache-Control': 'no-store'})

    @api.get('/api/research-starter')
    async def read(session: str, request: Request):
        if not window_allowed(request):
            return JSONResponse({'error': 'Open this brief in the Hooke window.'}, status_code=403)
        if session != built.day.sid:
            return JSONResponse({'error': 'This Project is no longer open.'}, status_code=409)
        return JSONResponse({'session': session, 'starter': built.day.research_starter},
                            headers={'Cache-Control': 'no-store'})
