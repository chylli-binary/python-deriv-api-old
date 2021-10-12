from deriv_api.cache import Cache
from deriv_api.deriv_api_calls import DerivAPICalls
from deriv_api.in_memory import InMemory
from deriv_api.subscription_manager import  SubscriptionManager
import websockets
import json
import logging
from deriv_api.errors import APIError, ConstructionError
from deriv_api.utils import dict_to_cache_key, is_valid_url
import re
from rx.subject import Subject
from deriv_api.custom_future import CustomFuture
from typing import Optional

logging.basicConfig(
    format="%(asctime)s %(message)s",
    level=logging.ERROR
)

class DerivAPI(DerivAPICalls):
    """
    The minimum functionality provided by DerivAPI, provides direct calls to the API.
    `api.cache` is available if you want to use the cached data

    example
    apiFromEndpoint = deriv_api.DerivAPI({ endpoint: 'ws.binaryws.com', app_id: 1234 });

    param {Object}     options
    param {WebSocket}  options.connection - A ready to use connection
    param {String}     options.endpoint   - API server to connect to
    param {Number}     options.app_id     - Application ID of the API user
    param {String}     options.lang       - Language of the API communication
    param {String}     options.brand      - Brand name
    param {Object}     options.middleware - A middleware to call on certain API actions

    property {Cache} cache - Temporary cache default to {InMemory}
    property {Cache} storage - If specified, uses a more persistent cache (local storage, etc.)
    """
    storage = ''
    def __init__(self, **options):
        endpoint = options.get('endpoint', 'frontend.binaryws.com')
        lang = options.get('lang', 'EN')
        brand = options.get('brand', '')
        cache = options.get('cache', InMemory())
        storage = options.get('storage')
        self.wsconnection = None

        if options.get('connection'):
            self.wsconnection = options.get('connection')
        else:
            if not options.get('app_id'):
                raise ConstructionError('An app_id is required to connect to the API')

            connection_argument = {
                'app_id': str(options.get('app_id')),
                'endpoint_url': self.get_url(endpoint),
                'lang': lang,
                'brand': brand
            }
            self.__set_apiURL(connection_argument)
            self.shouldReconnect = True

        if storage:
            self.storage = Cache(self, storage)

        self.req_id = 0
        self.pending_requests = {}
        self.connected = CustomFuture()
        self.subscription_manager: SubscriptionManager = SubscriptionManager(self)

        # If we have the storage look that one up
        self.cache = Cache(self.storage if self.storage else self, cache)

    def __set_apiURL(self, connection_argument):
        self.api_url = connection_argument.get('endpoint_url')+"/websockets/v3?app_id="+connection_argument.get('app_id')+"&l="+connection_argument.get('lang')+"&brand="+connection_argument.get('brand')

    def __get_apiURL(self):
        return self.api_url

    def get_url(self, original_endpoint):
        if not isinstance(original_endpoint, str):
            raise ConstructionError(f"Endpoint must be a string, passed: {type(original_endpoint)}")

        match = re.match(r'((?:\w*:\/\/)*)(.*)', original_endpoint).groups()
        protocol = match[0] if match[0] == "ws://" else "wss://"
        endpoint = match[1]

        url = protocol+endpoint
        if not is_valid_url(url):
            raise ConstructionError(f'Invalid URL:{original_endpoint}')

        return url

    async def api_connect(self):
        if not self.wsconnection and self.shouldReconnect:
            self.wsconnection = await websockets.connect(self.api_url)

        return self.wsconnection

    async def send(self, message):
        try:
            response = await self.send_receive(message)
        except (websockets.ConnectionClosed, websockets.ConnectionClosedError):
            if not self.shouldReconnect:
               return APIError("API Connection Closed")
            else:
                self.wsconnection = None
                await self.api_connect()
                response = await self.send_receive(message)

        await self.cache.set(message, response)
        if self.storage:
            self.storage.set(message, response)
        return response

    async def send_receive(self, message):
        websocket = await self.api_connect()
        await websocket.send(json.dumps(message))
        async for response in websocket:
            if response is None:
                self.wsconnection = None
                await self.send_receive()
            return self.parse_response(response)

    async def subscribe(self, request):
        return await self.subscription_manager.subscribe(request)

    def is_connection_closed(self):
        return self.connection.ready_state == 2 or self.connection.ready_state == 3


    # TODO
    # 1 add all funcs taht include subscription_manager
    # 2. check all functs that subscription_manager will called
    # 3. check async on all funcs of 1 and 2
    # 4. some function like "send" or manager `create_new_source` will await the first response
    # 5. make sure that first response can be got by other subscription
    # 6. dict.get(key, value) to set value
    def send_and_get_source(self, request: dict):
        pending = Subject()
        if 'req_id' not in request:
            self.req_id += 1
            request['req_id'] = self.req_id
        self.pending_requests[request['req_id']] = pending
        def connected_cb():
            if self.is_connection_closed():
                return CustomFuture().set_result(1)
            return self.connection.send(JSON.stringify(request))
        self.connected.then(connected_cb).catch(lambda e: pending.on_error(e))
        return pending

    def parse_response(self, message):
        data = json.loads(message)
        return data

    async def disconnect(self):
        self.shouldReconnect = False
        await self.wsconnection.close()
