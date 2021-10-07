import asyncio

from deriv_api.utils import dict_to_cache_key
from deriv_api.errors import APIError
from rx import operators as op
from rx.subject import Subject
from rx import Observable
from typing import Optional

# streams_list is the list of subscriptions msg_types available.
# Please add / remove based on current available streams in api.
# Refer https: // developers.binary.com /
# TODO auto generate this one
streams_list = ['balance', 'candles', 'p2p_advertiser', 'p2p_order', 'proposal',
                'proposal_array', 'proposal_open_contract', 'ticks', 'ticks_history', 'transaction',
                'website_status', 'buy']

# TODO test no such type

class SubscriptionManager:
    def __init__(self, api):
        self.api = api
        self.sources: dict = {}
        self.orig_sources: dict = {}
        self.subs_id_to_key: dict = {}
        self.key_to_subs_id: dict = {}
        self.buy_key_to_contract_id: dict = {}
        self.subs_per_msg_type: dict = {}

    async def subscribe(self, request: dict) -> Subject:
        """
        Subscribe to a given request, returns a stream of new responses,
        Errors should be handled by the user of the stream

        example
        const ticks = api.subscribe({ 'ticks': 'R_100' });
        ticks.subscribe(log) // Print every new tick

        param {Object} request - A request object acceptable by the API

        returns {Observable} - An RxPY SObservable
        """
        if self.source_exists(request):
            return self.get_source(request)

        new_request: dict = request.copy()
        new_request['subscribe'] = 1
        return await self.create_new_source(new_request)

    def get_source(self, request: dict) -> Optional[Subject]:
        key: str = dict_to_cache_key(request)
        if key in self.sources:
            return self.sources[key]

        # if we have a buy subscription reuse that for poc
        for c in self.buy_key_to_contract_id.values():
            if c['contract_id'] == request['contract_id']:
                return self.sources[c['buy_key']]

        return None

    def source_exists(self, request: dict):
        return self.get_source(request)

    async def create_new_source(self, request: dict) -> Subject:
        key: str = dict_to_cache_key(request)

        def forget_old_source():
            if key not in self.key_to_subs_id:
                return
            # noinspection PyBroadException
            try:
                self.forget(self.key_to_subs_id[key])
            except Exception:
                pass
            return

        # TODO test this
        self.orig_sources[key]: Subject = await self.api.send_and_get_source(request)
        source: Observable = self.orig_sources[key].pipe(
            op.finally_action(forget_old_source),
            op.share()
        )
        self.sources[key] = source
        self.save_subs_per_msg_type(request, key)
        print("before process_response")
        async def process_response():
            print("in process_response")
            # noinspection PyBroadException
            try:
                print("in try before await")
                response = await source.pipe(op.first(), op.to_future())
                print(f"in try {response}")
                if request.get('buy'):
                    self.buy_key_to_contract_id[key] = {
                        'contract_id': response['buy']['contract_id'],
                        'buy_key': key
                    }
                self.save_subs_id(key, response['subscription'])
            except Exception as err:
                print(f"get exception {err}")
                self.remove_key_on_error(key)


        await process_response()
        # TODO wait to_future directly
        # TODO no wait
        #asyncio.wait(task)
        return source

    def forget(self, sub_id):
        self.complete_subs_by_ids(sub_id)
        return self.api.send({'forget': sub_id})

    def forget_all(self, *types):
        # To include subscriptions that were automatically unsubscribed
        # for example a proposal subscription is auto-unsubscribed after buy

        for t in types:
            for k in (self.subs_per_msg_type[t] or []):
                self.complete_subs_by_key(k)
            self.subs_per_msg_type[t] = []
        return self.api.send({'forget_all': types})

    def complete_subs_by_ids(self, *sub_ids):
        print("..........................................")
        print(self.subs_id_to_key)
        for sub_id in sub_ids:
            if sub_id in self.subs_id_to_key:
                key = self.subs_id_to_key[sub_id]
                self.complete_subs_by_key(key)

    def save_subs_id(self, key, subscription):
        print(f"in save_subs_id {subscription}")
        if not subscription:
            return self.complete_subs_by_key(key)

        subs_id = subscription['id']
        if subs_id not in self.subs_id_to_key:
            self.subs_id_to_key[subs_id] = key
            self.key_to_subs_id[key] = subs_id

        print(f"save subs id result  {self.subs_id_to_key} {subs_id}")
        return None

    def save_subs_per_msg_type(self, request, key):
        msg_type = get_msg_type(request)
        if msg_type:
            self.subs_per_msg_type[msg_type] = self.subs_per_msg_type.get(msg_type) or []
            self.subs_per_msg_type[msg_type].append(key)
        else:
            self.api.sanity_errors.next(APIError('Subscription type is not found in deriv-api'))

    def remove_key_on_error(self, key):
        return lambda: self.complete_subs_by_key(key)

    def complete_subs_by_key(self, key):
        if not key or not self.sources[key]:
            return

        # Delete the source
        del self.sources[key]
        orig_source: Subject = self.orig_sources.pop(key)

        try:
            # Delete the subs id if exist
            if(key in self.key_to_subs_id):
                subs_id = self.key_to_subs_id[key]
                del self.subs_id_to_key[subs_id]
                # Delete the key
                del self.key_to_subs_id[key]

            # Delete the buy key to contract_id mapping
            del self.buy_key_to_contract_id[key]
        except KeyError:
            pass

        # Mark the source complete
        # TODO is it complete ?
        # MUST resolve
        orig_source.on_completed()
        orig_source.dispose()


def get_msg_type(request) -> str:
    return next((x for x in streams_list if x in request), None)
