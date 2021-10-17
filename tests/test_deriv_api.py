import asyncio

import pytest
import pytest_mock
from deriv_api import deriv_api
from deriv_api.errors import APIError, ConstructionError, ResponseError
from deriv_api.custom_future import CustomFuture
from rx.subject import Subject
import rx.operators as op
import pickle
import json

class MockedWs:
    def __init__(self):
        self.data = []
        self.called = {'send': [], 'recv' : []}
        self.slept_at = 0
        self.queue = Subject()
        self.req_res_map = {}
        async def build_queue():
            while 1:
                await asyncio.sleep(0.01)
                # make queue
                for idx, d in enumerate(self.data):
                    if d is None:
                        continue
                    await asyncio.sleep(0.01) # TODO delete this line
                    print(f"emit in ws{d}")
                    self.queue.on_next(json.dumps(d))
                    # if subscription, then we keep it
                    if not d.get('subscription'):
                        self.data[idx] = None
        self.task_build_queue = asyncio.create_task(build_queue())
    async def send(self, request):
        print(f"calling send {request}")
        self.called['send'].append(request)
        request = json.loads(request)
        new_request = request.copy()
        # req_id will be generated by api automatically
        req_id = new_request.pop('req_id')
        print(f"in send generate key of {new_request}")
        key = pickle.dumps(new_request)
        response = self.req_res_map.get(key)
        print(f"in send resosne is {response}")
        if response:
            response['req_id'] = req_id
            self.data.append(response)
            self.req_res_map.pop(key)
        forget_id = request.get('forget')
        if forget_id:
            for idx, d in enumerate(self.data):
                if d is None:
                    continue
                subscription_data = d.get('subscription')
                if subscription_data and subscription_data['id'] == forget_id:
                    self.data[idx] = None
                    break


    async def recv(self):
        self.called['recv'].append(None)
        print("call recv")
        data = await self.queue.pipe(op.first(),op.to_future())
        print("recv in test")
        return data

    def add_data(self,response):
        request = response['echo_req'].copy()
        # req_id will be added by api automatically
        # we remove it here for consistence
        request.pop('req_id', None)
        print(f"in add data key is {request}")
        key = pickle.dumps(request)
        print(f"in add data key is {key}")
        self.req_res_map[key] = response

    def clear(self):
        self.task_build_queue.cancel('end')

def test_connect_parameter():
    with pytest.raises(ConstructionError, match=r"An app_id is required to connect to the API"):
        deriv_api_obj = deriv_api.DerivAPI(endpoint=5432)

    with pytest.raises(ConstructionError, match=r"Endpoint must be a string, passed: <class 'int'>"):
        deriv_api_obj = deriv_api.DerivAPI(app_id=1234, endpoint=5432)

    with pytest.raises(ConstructionError, match=r"Invalid URL:local123host"):
        deriv_api_obj = deriv_api.DerivAPI(app_id=1234, endpoint='local123host')

@pytest.mark.asyncio
async def test_deriv_api(mocker):
    mocker.patch('deriv_api.deriv_api.DerivAPI.api_connect', return_value='')
    deriv_api_obj = deriv_api.DerivAPI(app_id=1234, endpoint='localhost')
    assert(isinstance(deriv_api_obj, deriv_api.DerivAPI))
    await deriv_api_obj.clear()

@pytest.mark.asyncio
async def test_get_url(mocker):
    deriv_api_obj = get_deriv_api(mocker)
    assert deriv_api_obj.get_url("localhost") == "wss://localhost"
    assert deriv_api_obj.get_url("ws://localhost") == "ws://localhost"
    with pytest.raises(ConstructionError, match=r"Invalid URL:testurl"):
        deriv_api_obj.get_url("testurl")
    await deriv_api_obj.clear()

def get_deriv_api(mocker):
    mocker.patch('deriv_api.deriv_api.DerivAPI.api_connect', return_value=CustomFuture().set_result(1))
    deriv_api_obj = deriv_api.DerivAPI(app_id=1234, endpoint='localhost')
    return deriv_api_obj

@pytest.mark.asyncio
async def test_transform_none_to_future():
    loop = asyncio.get_event_loop()
    f = loop.create_future()
    trans_f = deriv_api.transform_none_to_future(f)
    f.set_result(True)
    await asyncio.sleep(0.01)
    assert trans_f.is_resolved()
    f = loop.create_future()
    trans_f = deriv_api.transform_none_to_future(f)
    f.set_result(None)
    await asyncio.sleep(0.01)
    assert trans_f.is_pending()

@pytest.mark.asyncio
async def test_mocked_ws():
    wsconnection = MockedWs()
    data1 = {"echo_req":{"ticks" : 'R_50', 'req_id': 1} ,"msg_type": "ticks", "req_id": 1, "subscription": {"id": "world"}}
    data2 = {"echo_req":{"ping": 1, 'req_id': 2},"msg_type": "ping", "pong": 1, "req_id": 2}
    wsconnection.add_data(data1)
    wsconnection.add_data(data2)
    await wsconnection.send(json.dumps(data1["echo_req"]))
    await wsconnection.send(json.dumps(data2["echo_req"]))
    assert json.loads(await wsconnection.recv()) == data1, "we can get first data"
    assert json.loads(await wsconnection.recv()) == data2, "we can get second data"
    assert json.loads(await wsconnection.recv()) == data1, "we can still get first data becaues it is a subscription"
    assert json.loads(await wsconnection.recv()) == data1, "we will not get second data because it is not a subscription"
    assert len(wsconnection.called['send']) == 2
    assert len(wsconnection.called['recv']) == 4
    wsconnection.clear()

@pytest.mark.asyncio
async def test_simple_send():
    wsconnection = MockedWs()
    api = deriv_api.DerivAPI(connection = wsconnection)
    data1 = {"echo_req":{"ping": 1},"msg_type": "ping", "pong": 1}
    data2 = {"echo_req":{"ticks" : 'R_50'} ,"msg_type": "ticks"}
    wsconnection.add_data(data1)
    wsconnection.add_data(data2)
    res1 = data1.copy()
    add_req_id(res1, 1)
    res2 = data2.copy()
    add_req_id(res2, 2)
    assert await api.send(data1['echo_req']) == res1
    assert await api.ticks(data2['echo_req']) == res2
    assert len(wsconnection.called['send']) == 2
    wsconnection.clear()
    await api.clear()

@pytest.mark.asyncio
async def test_subscription():
    wsconnection = MockedWs()
    api = deriv_api.DerivAPI(connection=wsconnection)
    r50_data = {
        'echo_req': {'ticks': 'R_50', 'subscribe': 1},
        'msg_type': 'tick',
        'subscription': {'id': 'A11111'}
    }
    r100_data = {
        'echo_req': {'ticks': 'R_100', 'subscribe': 1},
        'msg_type': 'tick',
        'subscription': {'id': 'A22222'}
    }
    wsconnection.add_data(r50_data)
    wsconnection.add_data(r100_data)
    r50_req = r50_data['echo_req']
    r50_req.pop('subscribe');
    r100_req = r100_data['echo_req']
    r100_req.pop('subscribe');
    sub1 = await api.subscribe(r50_req)
    sub2 = await api.subscribe(r100_req)
    f1 = sub1.pipe(op.take(2), op.to_list(), op.to_future())
    f2 = sub2.pipe(op.take(2), op.to_list(), op.to_future())
    result = await asyncio.gather(f1, f2)
    assert result == [[r50_data, r50_data], [r100_data, r100_data]]
    await asyncio.sleep(0.01)  # wait sending 'forget' finished
    assert wsconnection.called['send'] == [
        '{"ticks": "R_50", "subscribe": 1, "req_id": 1}',
        '{"ticks": "R_100", "subscribe": 1, "req_id": 2}',
        '{"forget": "A11111", "req_id": 3}',
        '{"forget": "A22222", "req_id": 4}']


    wsconnection.clear()
    await api.clear()

@pytest.mark.asyncio
async def test_extra_response():
    wsconnection = MockedWs()
    api = deriv_api.DerivAPI(connection=wsconnection)
    error = None
    async def get_sanity_error():
        nonlocal error
        error = await api.sanity_errors.pipe(op.first(),op.to_future())
    error_task = asyncio.create_task(get_sanity_error())
    wsconnection.data.append({"hello":"world"})
    try:
        await asyncio.wait_for(error_task, timeout=0.1)
        assert str(error) == 'APIError:Extra response'
    except asyncio.exceptions.TimeoutError:
        assert False, "error data apppear timeout "
    wsconnection.clear()
    await api.clear()

@pytest.mark.asyncio
async def test_response_error():
    wsconnection = MockedWs()
    api = deriv_api.DerivAPI(connection=wsconnection)
    r50_data = {
        'echo_req': {'ticks': 'R_50', 'subscribe': 1},
        'msg_type': 'tick',
        'error': {'code': 'TestError', 'message': 'test error message'}
    }
    wsconnection.add_data(r50_data)
    sub1 = await api.subscribe(r50_data['echo_req'])
    f1 = sub1.pipe(op.first(), op.to_future())
    with pytest.raises(ResponseError, match='ResponseError: test error message'):
        await f1
    r50_data = {
        'echo_req': {'ticks': 'R_50', 'subscribe': 1},
        'msg_type': 'tick',
        'req_id': f1.exception().req_id,
        'subscription': {'id': 'A111111'}
    }
    wsconnection.data.append(r50_data) # add back r50 again
    #will send a `forget` if get a response again
    await asyncio.sleep(0.1)
    assert wsconnection.called['send'][-1] == '{"forget": "A111111", "req_id": 2}'

    #poc_data = {
    #    'echo_req': {'proposal_open_contract': 1, 'subscribe': 1}
    #    'msg_type': 'proposal_open_contract',
    #    'error': {'code': 'TestError', 'message': 'test error message'}
    #}
    wsconnection.clear()
    await api.clear()

def add_req_id(response, req_id):
    response['echo_req']['req_id'] = req_id
    response['req_id'] = req_id
    return response