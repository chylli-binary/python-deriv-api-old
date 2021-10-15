from deriv_api.cache import Cache
from deriv_api.deriv_api_calls import DerivAPICalls
from deriv_api.in_memory import InMemory
import websockets
import json
import logging
from deriv_api.errors import APIError, ConstructionError
from deriv_api.utils import dict_to_cache_key, is_valid_url
import re
from typing import Union

logging.basicConfig(
    format="%(asctime)s %(message)s",
    level=logging.ERROR
)

__pdoc__ = {
    'deriv_api.deriv_api.DerivAPI.send': False,
    'deriv_api.deriv_api.DerivAPI.api_connect': False,
    'deriv_api.deriv_api.DerivAPI.get_url': False,
    'deriv_api.deriv_api.DerivAPI.parse_response': False,
    'deriv_api.deriv_api.DerivAPI.send_receive': False,
    'deriv_api.deriv_api.DerivAPI.wsconnection' : False,
    'deriv_api.deriv_api.DerivAPI.storage' : False   
}

class DerivAPI(DerivAPICalls):
    """
    The minimum functionality provided by DerivAPI, provides direct calls to the API.
    `api.cache` is available if you want to use the cached data

    Examples
    --------
    - Pass the arguments needed to create a connection:
    >>> api = deriv_api.DerivAPI({ endpoint: 'ws://...', app_id: 1234 });

    - create and use a previously opened connection:
    >>> connection = await websockets.connect('ws://...')
    >>> api = deriv_api.DerivAPI(connection=connection)

    Args:
        options (dict): 

    Parameters
    ----------
        options.connection : websockets.WebSocketClientProtocol
            A ready to use connection
        options.endpoint : String
            API server to connect to
        options.app_id : String
            Application ID of the API user
        options.lang : String
            Language of the API communication
        options.brand : String
            Brand name
        options.middleware : String
            A middleware to call on certain API actions

    Properties
    cache : Cache
        Temporary cache default to {InMemory}
    storage : Cache
        If specified, uses a more persistent cache (local storage, etc.)
    """

    wsconnection: Union[websockets.WebSocketClientProtocol, None] = None
    storage: Union[InMemory, Cache, str] = ''

    def __init__(self, **options: str) -> None:
        endpoint = options.get('endpoint', 'frontend.binaryws.com')
        lang = options.get('lang', 'EN')
        brand = options.get('brand', '')
        cache = options.get('cache', InMemory())
        storage = options.get('storage')

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

        # If we have the storage look that one up
        self.cache = Cache(self.storage if self.storage else self, cache)

    def __set_apiURL(self, connection_argument: dict) -> None:
        self.api_url = connection_argument.get('endpoint_url') + "/websockets/v3?app_id=" + connection_argument.get(
            'app_id') + "&l=" + connection_argument.get('lang') + "&brand=" + connection_argument.get('brand')

    def __get_apiURL(self) -> str:
        return self.api_url

    def get_url(self, original_endpoint: str) -> Union[str, ConstructionError]:
        if not isinstance(original_endpoint, str):
            raise ConstructionError(f"Endpoint must be a string, passed: {type(original_endpoint)}")

        match = re.match(r'((?:\w*:\/\/)*)(.*)', original_endpoint).groups()
        protocol = match[0] if match[0] == "ws://" else "wss://"
        endpoint = match[1]

        url = protocol + endpoint
        if not is_valid_url(url):
            raise ConstructionError(f'Invalid URL:{original_endpoint}')

        return url

    async def api_connect(self) -> websockets.WebSocketClientProtocol:
        if not self.wsconnection and self.shouldReconnect:
            self.wsconnection = await websockets.connect(self.api_url)

        return self.wsconnection

    async def send(self, message: dict) -> dict:
        try:
            response = await self.send_receive(message)
        except (websockets.ConnectionClosed, websockets.ConnectionClosedError):
            if not self.shouldReconnect:
                return APIError("API Connection Closed")
            else:
                self.wsconnection = ''
                await self.api_connect()
                response = await self.send_receive(message)

        await self.cache.set(message, response)
        if self.storage:
            await self.storage.set(message, response)
        return response

    async def send_receive(self, message: dict) -> dict:
        websocket = await self.api_connect()
        await websocket.send(json.dumps(message))
        async for response in websocket:
            if response is None:
                self.wsconnection = ''
                await self.send_receive(message)
            return self.parse_response(response)

    def parse_response(self, message: str) -> dict:
        data = json.loads(message)
        return data

    async def disconnect(self) -> None:
        """ To disconnect the websocket connection
        """
        self.shouldReconnect = False
        await self.wsconnection.close()
